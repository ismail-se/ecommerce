# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import datetime
import json
from uuid import uuid4

import ddt
import httpretty
import mock
from django.urls import reverse
from django.utils.timezone import now
from oscar.core.loading import get_model
from rest_framework import status
from waffle.models import Switch

from ecommerce.coupons.tests.mixins import CouponMixin, DiscoveryMockMixin
from ecommerce.enterprise.benefits import BENEFIT_MAP as ENTERPRISE_BENEFIT_MAP
from ecommerce.enterprise.conditions import AssignableEnterpriseCustomerCondition
from ecommerce.enterprise.constants import ENTERPRISE_OFFERS_FOR_COUPONS_SWITCH
from ecommerce.enterprise.tests.mixins import EnterpriseServiceMockMixin
from ecommerce.extensions.catalogue.tests.mixins import DiscoveryTestMixin
from ecommerce.invoice.models import Invoice
from ecommerce.programs.custom import class_path
from ecommerce.tests.mixins import ThrottlingMixin
from ecommerce.tests.testcases import TestCase

Basket = get_model('basket', 'Basket')
Benefit = get_model('offer', 'Benefit')
Product = get_model('catalogue', 'Product')
Voucher = get_model('voucher', 'Voucher')

ENTERPRISE_COUPONS_LINK = reverse('api:v2:enterprise-coupons-list')


class TestEnterpriseCustomerView(EnterpriseServiceMockMixin, TestCase):

    dummy_enterprise_customer_data = {
        'results': [
            {
                'name': 'Starfleet Academy',
                'uuid': '5113b17bf79f4b5081cf3be0009bc96f',
                'hypothetical_private_info': 'seriously, very private',
            },
            {
                'name': 'Millennium Falcon',
                'uuid': 'd1fb990fa2784a52a44cca1118ed3993',
            }
        ]
    }

    @mock.patch('ecommerce.enterprise.utils.EdxRestApiClient')
    @httpretty.activate
    def test_get_customers(self, mock_client):
        self.mock_access_token_response()
        instance = mock_client.return_value
        setattr(
            instance,
            'enterprise-customer',
            mock.MagicMock(
                get=mock.MagicMock(
                    return_value=self.dummy_enterprise_customer_data
                )
            ),
        )
        url = reverse('api:v2:enterprise:enterprise_customers')
        result = self.client.get(url)
        self.assertEqual(result.status_code, status.HTTP_401_UNAUTHORIZED)

        user = self.create_user(is_staff=True)

        self.client.login(username=user.username, password=self.password)

        result = self.client.get(url)
        self.assertEqual(result.status_code, status.HTTP_200_OK)
        self.assertJSONEqual(
            result.content.decode('utf-8'),
            {
                'results': [
                    {
                        'name': 'Millennium Falcon',
                        'id': 'd1fb990fa2784a52a44cca1118ed3993'
                    },
                    {
                        'name': 'Starfleet Academy',
                        'id': '5113b17bf79f4b5081cf3be0009bc96f'
                    }  # Note that the private information from the API has been stripped
                ]
            }
        )


@ddt.ddt
class EnterpriseCouponViewSetTest(CouponMixin, DiscoveryTestMixin, DiscoveryMockMixin, ThrottlingMixin, TestCase):
    """
    Test the enterprise coupon order functionality.
    """
    def setUp(self):
        super(EnterpriseCouponViewSetTest, self).setUp()
        self.user = self.create_user(is_staff=True)
        self.client.login(username=self.user.username, password=self.password)

        self.data = {
            'benefit_type': Benefit.PERCENTAGE,
            'benefit_value': 100,
            'category': {'name': self.category.name},
            'code': '',
            'end_datetime': str(now() + datetime.timedelta(days=10)),
            'price': 100,
            'quantity': 2,
            'start_datetime': str(now() - datetime.timedelta(days=10)),
            'title': 'Tešt Enterprise čoupon',
            'voucher_type': Voucher.SINGLE_USE,
            'enterprise_customer': {'name': 'test enterprise', 'id': str(uuid4()).decode('utf-8')},
            'enterprise_customer_catalog': str(uuid4()).decode('utf-8'),
        }

    def get_coupon_voucher(self, coupon):
        """
        Helper method to get coupon voucher.
        """
        return coupon.attr.coupon_vouchers.vouchers.first()

    def get_coupon_data(self, coupon_title):
        """
        Helper method to return coupon data by coupon title.
        """
        coupon = Product.objects.get(title=coupon_title)
        return {
            'end_date': self.get_coupon_voucher_end_date(coupon),
            'has_error': False,
            'id': coupon.id,
            'max_uses': None,
            'num_codes': 2,
            'num_unassigned': 0,
            'num_uses': 0,
            'start_date': self.get_coupon_voucher_start_date(coupon),
            'title': coupon.title,
            'usage_limitation': 'Single use'
        }

    def get_coupon_voucher_start_date(self, coupon):
        """
        Helper method to return coupon voucher start date.
        """
        return self.get_coupon_voucher(coupon).start_datetime.isoformat().replace('+00:00', 'Z')

    def get_coupon_voucher_end_date(self, coupon):
        """
        Helper method to return coupon voucher end date.
        """
        return self.get_coupon_voucher(coupon).end_datetime.isoformat().replace('+00:00', 'Z')

    def get_response(self, method, path, data=None):
        """
        Helper method for sending requests and returning the response.
        """
        enterprise_id = ''
        enterprise_name = 'ToyX'
        if data and data.get('enterprise_customer'):
            enterprise_id = data['enterprise_customer']['id']
            enterprise_name = data['enterprise_customer']['name']

        with mock.patch(
            'ecommerce.extensions.voucher.utils.get_enterprise_customer',
            mock.Mock(return_value={
                'name': enterprise_name,
                'enterprise_customer_uuid': enterprise_id
            })
        ):
            if method == 'GET':
                return self.client.get(path)
            elif method == 'POST':
                return self.client.post(path, json.dumps(data), 'application/json')
            elif method == 'PUT':
                return self.client.put(path, json.dumps(data), 'application/json')
        return None

    def test_list_enterprise_coupons(self):
        Switch.objects.update_or_create(name=ENTERPRISE_OFFERS_FOR_COUPONS_SWITCH, defaults={'active': True})
        self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        self.create_coupon()
        self.assertEqual(Product.objects.filter(product_class__name='Coupon').count(), 2)

        response = self.client.get(ENTERPRISE_COUPONS_LINK)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        coupon_data = json.loads(response.content)['results']
        self.assertEqual(len(coupon_data), 1)
        self.assertEqual(coupon_data[0]['title'], self.data['title'])
        self.assertEqual(coupon_data[0]['client'], self.data['enterprise_customer']['name'])
        self.assertEqual(coupon_data[0]['enterprise_customer'], self.data['enterprise_customer']['id'])
        self.assertEqual(coupon_data[0]['enterprise_customer_catalog'], self.data['enterprise_customer_catalog'])
        self.assertEqual(coupon_data[0]['code_status'], 'ACTIVE')

    def test_create_ent_offers_switch_off(self):
        Switch.objects.update_or_create(name=ENTERPRISE_OFFERS_FOR_COUPONS_SWITCH, defaults={'active': False})
        response = self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_ent_offers_switch_on(self):
        Switch.objects.update_or_create(name=ENTERPRISE_OFFERS_FOR_COUPONS_SWITCH, defaults={'active': True})
        response = self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        coupon = Product.objects.get(title=self.data['title'])
        enterprise_customer_id = self.data['enterprise_customer']['id']
        enterprise_name = self.data['enterprise_customer']['name']
        enterprise_catalog_id = self.data['enterprise_customer_catalog']
        vouchers = coupon.attr.coupon_vouchers.vouchers.all()
        for voucher in vouchers:
            all_offers = voucher.offers.all()
            self.assertEqual(len(all_offers), 1)
            offer = all_offers[0]
            self.assertEqual(str(offer.condition.enterprise_customer_uuid), enterprise_customer_id)
            self.assertEqual(str(offer.condition.enterprise_customer_catalog_uuid), enterprise_catalog_id)
            self.assertEqual(offer.condition.proxy_class, class_path(AssignableEnterpriseCustomerCondition))
            self.assertIsNone(offer.condition.range)
            self.assertEqual(offer.benefit.proxy_class, class_path(ENTERPRISE_BENEFIT_MAP[self.data['benefit_type']]))
            self.assertEqual(offer.benefit.value, self.data['benefit_value'])
            self.assertIsNone(offer.benefit.range)

        # Check that the enterprise name took precedence as the client name
        basket = Basket.objects.filter(lines__product_id=coupon.id).first()
        invoice = Invoice.objects.get(order__basket=basket)
        self.assertEqual(invoice.business_client.name, enterprise_name)
        self.assertEqual(str(invoice.business_client.enterprise_customer_uuid), enterprise_customer_id)

    def test_update_ent_offers_switch_off(self):
        Switch.objects.update_or_create(name=ENTERPRISE_OFFERS_FOR_COUPONS_SWITCH, defaults={'active': True})
        self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        coupon = Product.objects.get(title=self.data['title'])

        Switch.objects.update_or_create(name=ENTERPRISE_OFFERS_FOR_COUPONS_SWITCH, defaults={'active': False})
        response = self.get_response(
            'PUT',
            reverse('api:v2:enterprise-coupons-detail', kwargs={'pk': coupon.id}),
            data={
                'title': 'Updated Enterprise Coupon',
            }
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_ent_offers_switch_on(self):
        Switch.objects.update_or_create(name=ENTERPRISE_OFFERS_FOR_COUPONS_SWITCH, defaults={'active': True})
        self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        coupon = Product.objects.get(title=self.data['title'])

        self.get_response(
            'PUT',
            reverse('api:v2:enterprise-coupons-detail', kwargs={'pk': coupon.id}),
            data={
                'title': 'Updated Enterprise Coupon',
            }
        )
        updated_coupon = Product.objects.get(title='Updated Enterprise Coupon')
        self.assertEqual(coupon.id, updated_coupon.id)

    def test_update_non_ent_coupon(self):
        Switch.objects.update_or_create(name=ENTERPRISE_OFFERS_FOR_COUPONS_SWITCH, defaults={'active': True})
        coupon = self.create_coupon()
        response = self.get_response(
            'PUT',
            reverse('api:v2:enterprise-coupons-detail', kwargs={'pk': coupon.id}),
            data={
                'title': 'Updated Enterprise Coupon',
            }
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_migrated_ent_coupon(self):
        Switch.objects.update_or_create(name=ENTERPRISE_OFFERS_FOR_COUPONS_SWITCH, defaults={'active': False})
        self.data.update({
            'catalog_query': '*:*',
            'course_seat_types': ['verified'],
            'benefit_value': 20,
            'title': 'Migrated Enterprise Coupon',
        })
        self.get_response('POST', reverse('api:v2:coupons-list'), self.data)
        coupon = Product.objects.get(title='Migrated Enterprise Coupon')

        Switch.objects.update_or_create(name=ENTERPRISE_OFFERS_FOR_COUPONS_SWITCH, defaults={'active': True})
        new_catalog = str(uuid4()).decode('utf-8')
        self.get_response(
            'PUT',
            reverse('api:v2:enterprise-coupons-detail', kwargs={'pk': coupon.id}),
            data={
                'enterprise_customer_catalog': new_catalog,
                'benefit_value': 50,
                'title': 'Updated Enterprise Coupon',
            }
        )
        updated_coupon = Product.objects.get(title='Updated Enterprise Coupon')
        self.assertEqual(coupon.id, updated_coupon.id)
        vouchers = updated_coupon.attr.coupon_vouchers.vouchers.all()
        for voucher in vouchers:
            all_offers = voucher.offers.all()
            self.assertEqual(len(all_offers), 2)
            original_offer = all_offers[0]
            self.assertEqual(original_offer.benefit.value, 50)
            self.assertEqual(str(original_offer.condition.range.enterprise_customer_catalog), new_catalog)
            enterprise_offer = all_offers[1]
            self.assertEqual(enterprise_offer.benefit.value, 50)
            self.assertEqual(str(enterprise_offer.condition.enterprise_customer_catalog_uuid), new_catalog)

    def test_update_max_uses_single_use(self):
        Switch.objects.update_or_create(name=ENTERPRISE_OFFERS_FOR_COUPONS_SWITCH, defaults={'active': True})
        self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        coupon = Product.objects.get(title=self.data['title'])
        response = self.get_response(
            'PUT',
            reverse('api:v2:enterprise-coupons-detail', kwargs={'pk': coupon.id}),
            data={
                'max_uses': 5,
            }
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_max_uses_invalid_value(self):
        Switch.objects.update_or_create(name=ENTERPRISE_OFFERS_FOR_COUPONS_SWITCH, defaults={'active': True})
        self.data.update({
            'voucher_type': Voucher.MULTI_USE,
            'max_uses': 5,
        })
        self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        coupon = Product.objects.get(title=self.data['title'])
        response = self.get_response(
            'PUT',
            reverse('api:v2:enterprise-coupons-detail', kwargs={'pk': coupon.id}),
            data={
                'max_uses': -5,
            }
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def get_response_json(self, method, path, data=None):
        """
        Helper method for sending requests and returning JSON response content.
        """
        response = self.get_response(method, path, data)
        if response:
            return json.loads(response.content)
        return None

    @ddt.data(
        (
            '85b08dde-0877-4474-a4e9-8408fe47ce88',
            ['coupon-1', 'coupon-2']
        ),
        (
            'f5c9149f-8dce-4410-bb0f-85c0f2dda864',
            ['coupon-3']
        ),
        (
            'f5c9149f-8dce-4410-bb0f-85c0f2dda860',
            []
        ),
    )
    @ddt.unpack
    def test_get_enterprise_coupon_overview_data(self, enterprise_id, expected_coupons):
        """
        Test if we get correct enterprise coupoun overview data.
        """
        Switch.objects.update_or_create(name=ENTERPRISE_OFFERS_FOR_COUPONS_SWITCH, defaults={'active': True})
        coupons_data = [{
            'title': 'coupon-1',
            'enterprise_customer': {'name': 'LOTRx', 'id': '85b08dde-0877-4474-a4e9-8408fe47ce88'}
        }, {
            'title': 'coupon-2',
            'enterprise_customer': {'name': 'LOTRx', 'id': '85b08dde-0877-4474-a4e9-8408fe47ce88'}
        }, {
            'title': 'coupon-3',
            'enterprise_customer': {'name': 'HPx', 'id': 'f5c9149f-8dce-4410-bb0f-85c0f2dda864'}
        }]

        # Create coupons.
        for coupon_data in coupons_data:
            self.get_response('POST', ENTERPRISE_COUPONS_LINK, dict(self.data, **coupon_data))

        # Build expected results.
        expected_results = []
        for coupon_title in expected_coupons:
            expected_results.append(self.get_coupon_data(coupon_title))

        overview_response = self.get_response_json(
            'GET',
            reverse('api:v2:enterprise-coupon-overview-overview-list', kwargs={'enterprise_id': enterprise_id})
        )

        # Verify that we get correct number of results related enterprise id.
        self.assertEqual(overview_response['count'], len(expected_results))

        # Verify that we get correct results.
        for actual_result in overview_response['results']:
            self.assertIn(actual_result, expected_results)
