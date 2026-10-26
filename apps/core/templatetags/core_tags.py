from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()


@register.filter(name='subtract')
def subtract(value, arg):
    """Subtract the arg from the value."""
    try:
        val_dec = Decimal(str(value))
        arg_dec = Decimal(str(arg))
        return val_dec - arg_dec
    except (InvalidOperation, TypeError, ValueError):
        try:
            return float(value) - float(arg)
        except (TypeError, ValueError):
            return ''
