"""Whole features in a project of more than 50 files.

    py -3 -m training.vibe.features --check
    py -3 -m training.vibe.features --split dev --edge-tests --edge-think --out run.json

Holdout tasks each change one function or config file. This measures what they
cannot: finding the right files in a larger project and keeping several layers
consistent. Each feature is scored by hidden end-to-end tests that check only what
its request states. Every feature has a reference solution, so `--check` proves
the hidden tests can pass and that the untouched project fails them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from takumi.vibe.client import DEFAULT_MODEL, OllamaClient
from takumi.vibe.context import budget_for
from takumi.vibe.loop import Options, VibeRun
from takumi.vibe.platforms import Step

PY = [sys.executable, '-B']
# name, extra dataclass fields as (field, type, default source).
DOMAINS = {
    'product': [('price_cents', 'int', '0')], 'customer': [('email', 'str', "''")], 'supplier': [('country', 'str', "''")],
    'warehouse': [('city', 'str', "''")], 'employee': [('team', 'str', "''")], 'invoice': [('amount_cents', 'int', '0')],
    'payment': [('amount_cents', 'int', '0')], 'review': [('stars', 'int', '5')], 'ticket': [('priority', 'str', "'normal'")],
    'vendor': [('contact', 'str', "''")], 'shipment': [('carrier', 'str', "''")],
}


def _cap(name: str) -> str:
    return name[0].upper() + name[1:]


def _domain_files(d: str, fields: list[tuple[str, str, str]]) -> dict[str, str]:
    cls = _cap(d)
    field_lines = ''.join(f'    {name}: {kind} = {default}\n' for name, kind, default in fields)
    copy = ''.join(f"            {name}=payload.get('{name}', {default}),\n" for name, _, default in fields)
    view = ''.join(f"        '{name}': item.{name},\n" for name, _, _ in fields)
    return {
        f'models/{d}.py': f'from dataclasses import dataclass\n\n\n@dataclass\nclass {cls}:\n    id: int\n    name: str\n{field_lines}',
        f'repos/{d}_repo.py': f'from repos.base import Repo\n\n\nclass {cls}Repo(Repo):\n    label = \'{d}\'\n',
        f'services/{d}_service.py': (f'from errors import ValidationError\nfrom models.{d} import {cls}\n\n\nclass {cls}Service:\n    def __init__(self, repo):\n        self.repo = repo\n\n'
                                     f"    def create(self, payload):\n        if not payload.get('name'):\n            raise ValidationError('name required')\n        return self.repo.add({cls}(\n            id=0,\n            name=payload['name'],\n{copy}        ))\n\n"
                                     '    def get(self, item_id):\n        return self.repo.get(item_id)\n\n    def list(self):\n        return self.repo.list()\n\n    def remove(self, item_id):\n        self.repo.delete(item_id)\n'),
        f'views/{d}_view.py': f"def render_{d}(item):\n    return {{\n        'id': item.id,\n        'name': item.name,\n{view}    }}\n",
        f'handlers/{d}_handler.py': (f'from handlers.common import respond\nfrom views.{d}_view import render_{d}\n\n\n'
                                     f'def create_{d}(services, payload):\n    return respond(lambda: render_{d}(services.{d}s.create(payload)), ok=201)\n\n\n'
                                     f'def get_{d}(services, item_id):\n    return respond(lambda: render_{d}(services.{d}s.get(item_id)))\n\n\n'
                                     f'def list_{d}s(services):\n    return respond(lambda: [render_{d}(item) for item in services.{d}s.list()])\n\n\n'
                                     f'def delete_{d}(services, item_id):\n    return respond(lambda: services.{d}s.remove(item_id))\n'),
        f'tests/test_{d}.py': (f'import unittest\n\nfrom app import build_services\nfrom handlers.{d}_handler import create_{d}, get_{d}, list_{d}s\n\n\n'
                               f"class {cls}Tests(unittest.TestCase):\n    def test_create_and_get(self):\n        services = build_services()\n        made = create_{d}(services, {{'name': 'a'}})\n        self.assertEqual(made['status'], 201)\n"
                               f"        self.assertEqual(get_{d}(services, made['body']['id'])['body']['name'], 'a')\n        self.assertEqual(len(list_{d}s(services)['body']), 1)\n\n    def test_name_required(self):\n        self.assertEqual(create_{d}(build_services(), {{}})['status'], 400)\n\n    def test_missing(self):\n        self.assertEqual(get_{d}(build_services(), 99)['status'], 404)\n"),
    }


def project() -> dict[str, str]:
    files = {
        'README.md': '# Depot\n\nA small operations service. handlers call services, services call repos, views shape responses.\nRun the tests with `python -m unittest discover -s tests -t .`.\n',
        'errors.py': 'class NotFound(Exception):\n    pass\n\n\nclass ValidationError(Exception):\n    pass\n',
        'repos/__init__.py': '', 'models/__init__.py': '', 'services/__init__.py': '', 'views/__init__.py': '', 'handlers/__init__.py': '', 'tests/__init__.py': '',
        'repos/base.py': ("from errors import NotFound\n\n\nclass Repo:\n    label = 'item'\n\n    def __init__(self):\n        self.items = {}\n        self.next_id = 1\n\n"
                          '    def add(self, item):\n        item.id = self.next_id\n        self.next_id += 1\n        self.items[item.id] = item\n        return item\n\n'
                          "    def get(self, item_id):\n        if item_id not in self.items:\n            raise NotFound(f'{self.label} {item_id} not found')\n        return self.items[item_id]\n\n"
                          '    def list(self):\n        return list(self.items.values())\n\n    def delete(self, item_id):\n        self.get(item_id)\n        del self.items[item_id]\n'),
        'handlers/common.py': ("from errors import NotFound, ValidationError\n\n\ndef respond(action, ok=200):\n    try:\n        return {'status': ok, 'body': action()}\n"
                               "    except NotFound as exc:\n        return {'status': 404, 'body': {'error': str(exc)}}\n    except ValidationError as exc:\n        return {'status': 400, 'body': {'error': str(exc)}}\n"),
        'models/order.py': "from dataclasses import dataclass, field\n\n\n@dataclass\nclass Order:\n    id: int\n    name: str\n    customer_id: int = 0\n    items: list = field(default_factory=list)\n    total_cents: int = 0\n",
        'repos/order_repo.py': "from repos.base import Repo\n\n\nclass OrderRepo(Repo):\n    label = 'order'\n",
        'services/order_service.py': ("from errors import ValidationError\nfrom models.order import Order\n\n\nclass OrderService:\n    def __init__(self, repo, customers, products):\n        self.repo, self.customers, self.products = repo, customers, products\n\n"
                                      "    def create(self, payload):\n        customer = self.customers.get(payload.get('customer_id'))\n        items = payload.get('items') or []\n        if not items:\n            raise ValidationError('items required')\n        total = 0\n"
                                      "        for item in items:\n            product = self.products.get(item['product_id'])\n            total += product.price_cents * item['quantity']\n"
                                      "        return self.repo.add(Order(id=0, name=f'order for {customer.name}', customer_id=customer.id, items=list(items), total_cents=total))\n\n"
                                      '    def get(self, item_id):\n        return self.repo.get(item_id)\n\n    def list(self):\n        return self.repo.list()\n'),
        'views/order_view.py': "def render_order(item):\n    return {\n        'id': item.id,\n        'customer_id': item.customer_id,\n        'items': item.items,\n        'total_cents': item.total_cents,\n    }\n",
        'handlers/order_handler.py': ('from handlers.common import respond\nfrom views.order_view import render_order\n\n\ndef create_order(services, payload):\n    return respond(lambda: render_order(services.orders.create(payload)), ok=201)\n\n\n'
                                      'def get_order(services, item_id):\n    return respond(lambda: render_order(services.orders.get(item_id)))\n\n\ndef list_orders(services):\n    return respond(lambda: [render_order(item) for item in services.orders.list()])\n'),
        'tests/test_order.py': ("import unittest\n\nfrom app import build_services\nfrom handlers.customer_handler import create_customer\nfrom handlers.order_handler import create_order\nfrom handlers.product_handler import create_product\n\n\n"
                                "class OrderTests(unittest.TestCase):\n    def test_total(self):\n        services = build_services()\n        customer = create_customer(services, {'name': 'c'})['body']['id']\n        product = create_product(services, {'name': 'p', 'price_cents': 250})['body']['id']\n"
                                "        made = create_order(services, {'customer_id': customer, 'items': [{'product_id': product, 'quantity': 2}]})\n        self.assertEqual(made['body']['total_cents'], 500)\n"),
    }
    imports, build = [], []
    for name, fields in DOMAINS.items():
        files.update(_domain_files(name, fields))
        imports.append(f'from repos.{name}_repo import {_cap(name)}Repo\nfrom services.{name}_service import {_cap(name)}Service\n')
        build.append(f'        {name}s={_cap(name)}Service({_cap(name)}Repo()),\n')
    files['app.py'] = ('from types import SimpleNamespace\n\n' + ''.join(imports) + 'from repos.order_repo import OrderRepo\nfrom services.order_service import OrderService\n\n\ndef build_services():\n    services = SimpleNamespace(\n'
                       + ''.join(build) + '    )\n    services.orders = OrderService(OrderRepo(), services.customers, services.products)\n    return services\n')
    return files


HELP = ("import unittest\n\nfrom app import build_services\nfrom handlers.customer_handler import *\nfrom handlers.order_handler import *\nfrom handlers.product_handler import *\nfrom handlers.shipment_handler import *\n\n\n"
        "def order(services, price=1000, quantity=1, **extra):\n    customer = extra.pop('customer', None) or create_customer(services, {'name': 'c'})['body']['id']\n    product = create_product(services, {'name': 'p', 'price_cents': price})['body']['id']\n"
        "    return create_order(services, {'customer_id': customer, 'items': [{'product_id': product, 'quantity': quantity}], **extra})\n\n\nclass Hidden(unittest.TestCase):\n    def setUp(self):\n        self.s = build_services()\n\n")


def _hidden(body: str) -> str:
    return HELP + ''.join('    ' + line + '\n' if line else '\n' for line in body.strip('\n').split('\n'))


FEATURES = {
    'order-discount': {
        'split': 'dev',
        'prompt': "Add discount codes to orders. create_order accepts an optional discount_code in its payload. SAVE10 takes 10% off the order total and SAVE25 takes 25% off, rounded down to whole cents of discount. An unknown code returns status 400 with the error 'unknown code', and no order is saved. Order responses include discount_code, which is empty without a code, and discount_cents, and total_cents is the amount after the discount.",
        'hidden': """
def test_ten(self):
    body = order(self.s, price=999, discount_code='SAVE10')['body']
    self.assertEqual((body['discount_code'], body['discount_cents'], body['total_cents']), ('SAVE10', 99, 900))
def test_twenty_five(self):
    self.assertEqual(order(self.s, price=1000, quantity=2, discount_code='SAVE25')['body']['total_cents'], 1500)
def test_none(self):
    body = order(self.s, price=1000)['body']
    self.assertEqual((body['discount_code'], body['discount_cents'], body['total_cents']), ('', 0, 1000))
def test_unknown(self):
    made = order(self.s, discount_code='NOPE')
    self.assertEqual((made['status'], made['body']['error']), (400, 'unknown code'))
    self.assertEqual(list_orders(self.s)['body'], [])
def test_get_keeps_it(self):
    made = order(self.s, price=1000, discount_code='SAVE10')['body']
    self.assertEqual(get_order(self.s, made['id'])['body']['discount_cents'], 100)
""",
        'solution': {
            'models/order.py': [('    total_cents: int = 0\n', "    total_cents: int = 0\n    discount_code: str = ''\n    discount_cents: int = 0\n")],
            'services/order_service.py': [
                ('class OrderService:\n', "DISCOUNTS = {'SAVE10': 10, 'SAVE25': 25}\n\n\nclass OrderService:\n"),
                ("        return self.repo.add(Order(id=0, name=f'order for {customer.name}', customer_id=customer.id, items=list(items), total_cents=total))\n",
                 "        code = payload.get('discount_code') or ''\n        if code and code not in DISCOUNTS:\n            raise ValidationError('unknown code')\n        discount = total * DISCOUNTS.get(code, 0) // 100\n"
                 "        return self.repo.add(Order(id=0, name=f'order for {customer.name}', customer_id=customer.id, items=list(items), total_cents=total - discount, discount_code=code, discount_cents=discount))\n")],
            'views/order_view.py': [("        'total_cents': item.total_cents,\n", "        'total_cents': item.total_cents,\n        'discount_code': item.discount_code,\n        'discount_cents': item.discount_cents,\n")],
        },
    },
    'product-archive': {
        'split': 'dev',
        'prompt': "Products can be archived. Add a handler archive_product(services, item_id) in the product handlers that returns status 200 with the product. Archived products no longer appear in list_products, but get_product still returns them, and product responses include archived, which is false for new products. Archiving an unknown product returns 404. Archiving twice is allowed and changes nothing. An order for an archived product returns status 400 with the error 'product archived', and no order is saved.",
        'hidden': """
def make(self):
    return create_product(self.s, {'name': 'p', 'price_cents': 5})['body']['id']
def test_new(self):
    self.assertIs(get_product(self.s, self.make())['body']['archived'], False)
def test_archive(self):
    pid = self.make()
    made = archive_product(self.s, pid)
    self.assertEqual((made['status'], made['body']['archived']), (200, True))
    self.assertEqual(archive_product(self.s, pid)['status'], 200)
def test_hidden_from_list(self):
    keep, gone = self.make(), self.make()
    archive_product(self.s, gone)
    self.assertEqual([p['id'] for p in list_products(self.s)['body']], [keep])
    self.assertIs(get_product(self.s, gone)['body']['archived'], True)
def test_unknown(self):
    self.assertEqual(archive_product(self.s, 99)['status'], 404)
def test_order_refused(self):
    pid = self.make()
    archive_product(self.s, pid)
    customer = create_customer(self.s, {'name': 'c'})['body']['id']
    made = create_order(self.s, {'customer_id': customer, 'items': [{'product_id': pid, 'quantity': 1}]})
    self.assertEqual((made['status'], made['body']['error']), (400, 'product archived'))
    self.assertEqual(list_orders(self.s)['body'], [])
""",
        'solution': {
            'models/product.py': [('    price_cents: int = 0\n', '    price_cents: int = 0\n    archived: bool = False\n')],
            'services/product_service.py': [('    def list(self):\n        return self.repo.list()\n', "    def list(self):\n        return [item for item in self.repo.list() if not item.archived]\n\n    def archive(self, item_id):\n        item = self.repo.get(item_id)\n        item.archived = True\n        return item\n")],
            'views/product_view.py': [("        'price_cents': item.price_cents,\n", "        'price_cents': item.price_cents,\n        'archived': item.archived,\n")],
            'handlers/product_handler.py': [('def delete_product(', 'def archive_product(services, item_id):\n    return respond(lambda: render_product(services.products.archive(item_id)))\n\n\ndef delete_product(')],
            'services/order_service.py': [("            product = self.products.get(item['product_id'])\n", "            product = self.products.get(item['product_id'])\n            if product.archived:\n                raise ValidationError('product archived')\n")],
        },
    },
    'customer-tier': {
        'split': 'unseen',
        'prompt': "Customers get a tier: basic, silver or gold. create_customer accepts an optional tier, which defaults to basic, and customer responses include it. Any other tier returns status 400 with the error 'unknown tier'. Add a handler update_customer_tier(services, item_id, tier) in the customer handlers that returns status 200 with the customer, or 404 for an unknown customer, or 400 for a bad tier without changing the customer. Orders use the tier when created: silver customers get 5% off and gold customers get 15% off the total, rounded down to whole cents of discount. Order responses include tier_discount_cents, and total_cents is the amount after the discount.",
        'hidden': """
def test_default(self):
    self.assertEqual(create_customer(self.s, {'name': 'a'})['body']['tier'], 'basic')
def test_bad_create(self):
    made = create_customer(self.s, {'name': 'a', 'tier': 'iron'})
    self.assertEqual((made['status'], made['body']['error']), (400, 'unknown tier'))
def test_update(self):
    cid = create_customer(self.s, {'name': 'a'})['body']['id']
    made = update_customer_tier(self.s, cid, 'gold')
    self.assertEqual((made['status'], made['body']['tier']), (200, 'gold'))
    self.assertEqual(update_customer_tier(self.s, cid, 'iron')['status'], 400)
    self.assertEqual(get_customer(self.s, cid)['body']['tier'], 'gold')
    self.assertEqual(update_customer_tier(self.s, 99, 'gold')['status'], 404)
def test_gold_order(self):
    cid = create_customer(self.s, {'name': 'a', 'tier': 'gold'})['body']['id']
    body = order(self.s, price=999, customer=cid)['body']
    self.assertEqual((body['tier_discount_cents'], body['total_cents']), (149, 850))
def test_silver_and_basic(self):
    cid = create_customer(self.s, {'name': 'a', 'tier': 'silver'})['body']['id']
    self.assertEqual(order(self.s, price=1000, customer=cid)['body']['total_cents'], 950)
    body = order(self.s, price=1000)['body']
    self.assertEqual((body['tier_discount_cents'], body['total_cents']), (0, 1000))
""",
        'solution': {
            'models/customer.py': [("    email: str = ''\n", "    email: str = ''\n    tier: str = 'basic'\n")],
            'services/customer_service.py': [
                ('class CustomerService:\n', "TIERS = ('basic', 'silver', 'gold')\n\n\nclass CustomerService:\n"),
                ("        return self.repo.add(Customer(\n", "        if payload.get('tier', 'basic') not in TIERS:\n            raise ValidationError('unknown tier')\n        return self.repo.add(Customer(\n"),
                ("            email=payload.get('email', ''),\n", "            email=payload.get('email', ''),\n            tier=payload.get('tier', 'basic'),\n"),
                ('    def list(self):\n', "    def set_tier(self, item_id, tier):\n        item = self.repo.get(item_id)\n        if tier not in TIERS:\n            raise ValidationError('unknown tier')\n        item.tier = tier\n        return item\n\n    def list(self):\n")],
            'views/customer_view.py': [("        'email': item.email,\n", "        'email': item.email,\n        'tier': item.tier,\n")],
            'handlers/customer_handler.py': [('def delete_customer(', 'def update_customer_tier(services, item_id, tier):\n    return respond(lambda: render_customer(services.customers.set_tier(item_id, tier)))\n\n\ndef delete_customer(')],
            'models/order.py': [('    total_cents: int = 0\n', '    total_cents: int = 0\n    tier_discount_cents: int = 0\n')],
            'services/order_service.py': [
                ('class OrderService:\n', "TIER_PERCENT = {'silver': 5, 'gold': 15}\n\n\nclass OrderService:\n"),
                ("        return self.repo.add(Order(id=0, name=f'order for {customer.name}', customer_id=customer.id, items=list(items), total_cents=total))\n",
                 "        discount = total * TIER_PERCENT.get(customer.tier, 0) // 100\n        return self.repo.add(Order(id=0, name=f'order for {customer.name}', customer_id=customer.id, items=list(items), total_cents=total - discount, tier_discount_cents=discount))\n")],
            'views/order_view.py': [("        'total_cents': item.total_cents,\n", "        'total_cents': item.total_cents,\n        'tier_discount_cents': item.tier_discount_cents,\n")],
        },
    },
    'shipment-tracking': {
        'split': 'unseen',
        'prompt': "Track shipment status. New shipments are pending. Add a handler advance_shipment(services, item_id, status) in the shipment handlers. A shipment may only move from pending to shipped and from shipped to delivered, and a pending or shipped shipment may also move to cancelled. A valid move returns status 200 with the shipment. Any other move returns status 409 with the error 'invalid move' and changes nothing, and an unknown shipment returns 404. Shipment responses include status and history, the list of statuses it has had in order, starting with pending. list_shipments takes an optional status argument and returns only shipments with that status.",
        'hidden': """
def make(self):
    return create_shipment(self.s, {'name': 'a'})['body']['id']
def test_new(self):
    body = get_shipment(self.s, self.make())['body']
    self.assertEqual((body['status'], body['history']), ('pending', ['pending']))
def test_moves(self):
    sid = self.make()
    self.assertEqual(advance_shipment(self.s, sid, 'shipped')['status'], 200)
    made = advance_shipment(self.s, sid, 'delivered')
    self.assertEqual((made['status'], made['body']['status'], made['body']['history']), (200, 'delivered', ['pending', 'shipped', 'delivered']))
def test_bad_move(self):
    sid = self.make()
    made = advance_shipment(self.s, sid, 'delivered')
    self.assertEqual((made['status'], made['body']['error']), (409, 'invalid move'))
    self.assertEqual(get_shipment(self.s, sid)['body']['history'], ['pending'])
def test_cancel(self):
    sid = self.make()
    self.assertEqual(advance_shipment(self.s, sid, 'cancelled')['status'], 200)
    self.assertEqual(advance_shipment(self.s, sid, 'shipped')['status'], 409)
    done = self.make()
    advance_shipment(self.s, done, 'shipped')
    advance_shipment(self.s, done, 'delivered')
    self.assertEqual(advance_shipment(self.s, done, 'cancelled')['status'], 409)
def test_unknown(self):
    self.assertEqual(advance_shipment(self.s, 99, 'shipped')['status'], 404)
def test_filter(self):
    one, two = self.make(), self.make()
    advance_shipment(self.s, two, 'shipped')
    self.assertEqual([s['id'] for s in list_shipments(self.s, status='shipped')['body']], [two])
    self.assertEqual(len(list_shipments(self.s)['body']), 2)
""",
        'solution': {
            'errors.py': [('class ValidationError(Exception):\n    pass\n', 'class ValidationError(Exception):\n    pass\n\n\nclass Conflict(Exception):\n    pass\n')],
            'handlers/common.py': [
                ('from errors import NotFound, ValidationError\n', 'from errors import Conflict, NotFound, ValidationError\n'),
                ("    except ValidationError as exc:\n", "    except Conflict as exc:\n        return {'status': 409, 'body': {'error': str(exc)}}\n    except ValidationError as exc:\n")],
            'models/shipment.py': [('from dataclasses import dataclass\n', 'from dataclasses import dataclass, field\n'), ("    carrier: str = ''\n", "    carrier: str = ''\n    status: str = 'pending'\n    history: list = field(default_factory=lambda: ['pending'])\n")],
            'services/shipment_service.py': [
                ('from errors import ValidationError\n', "from errors import Conflict, ValidationError\n"),
                ('class ShipmentService:\n', "MOVES = {'pending': ('shipped', 'cancelled'), 'shipped': ('delivered', 'cancelled')}\n\n\nclass ShipmentService:\n"),
                ('    def list(self):\n        return self.repo.list()\n', "    def list(self, status=None):\n        return [item for item in self.repo.list() if status is None or item.status == status]\n\n    def advance(self, item_id, status):\n        item = self.repo.get(item_id)\n        if status not in MOVES.get(item.status, ()):\n            raise Conflict('invalid move')\n        item.status = status\n        item.history.append(status)\n        return item\n")],
            'views/shipment_view.py': [("        'carrier': item.carrier,\n", "        'carrier': item.carrier,\n        'status': item.status,\n        'history': list(item.history),\n")],
            'handlers/shipment_handler.py': [
                ('def list_shipments(services):\n    return respond(lambda: [render_shipment(item) for item in services.shipments.list()])\n', 'def list_shipments(services, status=None):\n    return respond(lambda: [render_shipment(item) for item in services.shipments.list(status)])\n\n\ndef advance_shipment(services, item_id, status):\n    return respond(lambda: render_shipment(services.shipments.advance(item_id, status)))\n')],
        },
    },
}
HIDDEN_FILE = 'tests/test_hidden_feature.py'


def materialize(root: Path):
    for name, body in project().items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding='utf-8', newline='\n')


def steps() -> list[Step]:
    return [Step('tests', 'test', PY + ['-m', 'unittest', 'discover', '-s', 'tests', '-t', '.'], platform='python')]


def score(feature: str, root: Path) -> dict:
    """Runs the hidden tests in a copy, with this run's tests left out."""
    spec = FEATURES[feature]
    target = root / HIDDEN_FILE
    target.write_text(_hidden(spec['hidden']), encoding='utf-8', newline='\n')
    total = len(re.findall(r'^def test_', spec['hidden'], re.M))
    try:
        done = subprocess.run(PY + ['-m', 'unittest', 'tests.test_hidden_feature'], cwd=root, capture_output=True, text=True, timeout=120, encoding='utf-8', errors='replace')
    finally:
        target.unlink(missing_ok=True)
    ran = re.search(r'Ran (\d+) test', done.stderr)
    bad = sum(int(n) for n in re.findall(r'(?:failures|errors)=(\d+)', done.stderr))
    passed = max(0, int(ran.group(1)) - bad) if ran and int(ran.group(1)) == total else 0
    return {'passed': passed, 'total': total, 'tail': done.stderr[-500:] if passed < total else ''}


def apply_solution(feature: str, root: Path):
    for name, swaps in FEATURES[feature]['solution'].items():
        text = (root / name).read_text('utf-8')
        for old, new in swaps:
            assert old in text, (feature, name, old)
            text = text.replace(old, new, 1)
        (root / name).write_text(text, encoding='utf-8', newline='\n')


def check() -> list[dict]:
    rows = []
    for feature in FEATURES:
        with tempfile.TemporaryDirectory(prefix='feature-', ignore_cleanup_errors=True) as raw:
            root = Path(raw)
            materialize(root)
            visible = subprocess.run(steps()[0].argv, cwd=root, capture_output=True, text=True).returncode == 0
            before = score(feature, root)
            apply_solution(feature, root)
            solved_visible = subprocess.run(steps()[0].argv, cwd=root, capture_output=True, text=True).returncode == 0
            after = score(feature, root)
            rows.append({'id': feature, 'files': len(project()), 'visible_before': visible, 'visible_after': solved_visible,
                         'hidden_before': before['passed'], 'hidden_after': after['passed'], 'hidden_total': after['total'], 'solution_files': len(FEATURES[feature]['solution'])})
    return rows


def features_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def run_feature(feature: str, client, options: Options) -> dict:
    with tempfile.TemporaryDirectory(prefix='feature-', ignore_cleanup_errors=True) as raw:
        root = Path(raw) / 'p'
        root.mkdir()
        materialize(root)
        plan = {'platforms': ['python'], 'steps': steps(), 'runnable': 1, 'has_tests': True, 'can_prove': True, 'blocked': []}
        report = VibeRun(root, FEATURES[feature]['prompt'], client, plan=plan, options=options).run()
        hidden = score(feature, root)
        needed = set(FEATURES[feature]['solution'])
        changed = set(report['changed_files'])
    return {'id': feature, 'state': report['state'], 'hidden_passed': hidden['passed'], 'hidden_total': hidden['total'], 'hidden_tail': hidden['tail'],
            'files_changed': sorted(changed), 'solution_files_touched': len(needed & changed), 'solution_files': len(needed), 'attempts': report['attempts'],
            'seconds': report['seconds'], 'output_tokens': report['output_tokens'], 'detail': report['detail'][:400], 'edge_tests': report['edge_tests'],
            'edit_errors': [a['edit_error'][:200] for a in report['attempt_log'] if a.get('edit_error')], 'diff': report['diff'][:4000]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--split', choices=('dev', 'unseen'))
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--edge-tests', action='store_true')
    parser.add_argument('--edge-think', action='store_true')
    parser.add_argument('--edge-review', action='store_true')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps(check(), indent=1))
        return 0
    client = OllamaClient(args.model, budget=budget_for(args.model))
    report = {'split': args.split, 'features_sha256': features_sha256(), 'edge_tests': args.edge_tests, 'edge_think': args.edge_think, 'edge_review': args.edge_review,
              'model': args.model, 'started': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'files_in_project': len(project()), 'features': []}
    for feature, spec in FEATURES.items():
        if spec['split'] != args.split:
            continue
        options = Options(keep_failed=True, budget=client.budget, edge_tests=args.edge_tests, edge_think=args.edge_think, edge_review=args.edge_review)
        row = run_feature(feature, client, options)
        report['features'].append(row)
        report['score'] = {'hidden_passed': sum(r['hidden_passed'] for r in report['features']), 'hidden_total': sum(r['hidden_total'] for r in report['features'])}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
        # Unseen rows stay sealed until the product change is finished, so print no scores for them.
        print(feature, 'finished', str(row['seconds']) + 's', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
