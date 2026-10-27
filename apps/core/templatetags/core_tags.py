from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()


@register.filter(name='subtract')
def subtract(value, arg):
    """Subtract arg from value."""
    try:
        return Decimal(str(value)) - Decimal(str(arg))
    except (InvalidOperation, TypeError, ValueError):
        try:
            return float(value) - float(arg)
        except (TypeError, ValueError):
            return ''


@register.filter(name='get_item')
def get_item(dictionary, key):
    """Look up a dictionary value by key in templates: {{ mydict|get_item:key }}"""
    if dictionary is None:
        return None
    return dictionary.get(key)


@register.filter(name='pct_of')
def pct_of(value, total):
    """Return integer percentage of value/total, clamped 0-100."""
    try:
        v = Decimal(str(value))
        t = Decimal(str(total))
        if t <= 0:
            return 0
        return min(100, int(v / t * 100))
    except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
        return 0


@register.filter(name='multiply')
def multiply(value, arg):
    """Multiply value by arg."""
    try:
        return Decimal(str(value)) * Decimal(str(arg))
    except (InvalidOperation, TypeError, ValueError):
        return ''
