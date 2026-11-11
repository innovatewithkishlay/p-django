"""Small presentation helpers used across the templates."""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def tone(score):
    """Map a 0-100 score to a colour band used by CSS (t1 = worst … t5 = best)."""
    try:
        score = float(score)
    except (TypeError, ValueError):
        return "t0"
    if score >= 85:
        return "t5"
    if score >= 70:
        return "t4"
    if score >= 55:
        return "t3"
    if score >= 40:
        return "t2"
    return "t1"


@register.filter
def metric(value, kind):
    if value is None:
        return "—"
    if kind == "int":
        return f"{int(value):,}"
    if kind == "float":
        return f"{value:.1f}"
    if kind == "ratio":
        return f"{value:.2f}"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    return value


@register.filter
def signed(value, kind="int"):
    if value is None:
        return ""
    if value == 0:
        return "±0"
    sign = "+" if value > 0 else "−"
    magnitude = abs(value)
    if kind == "float":
        text = f"{magnitude:.1f}"
    elif kind == "ratio":
        text = f"{magnitude:.2f}"
    elif kind == "pct":
        text = f"{magnitude * 100:.1f} pt"
    else:
        text = f"{int(magnitude):,}"
    return f"{sign}{text}"


@register.filter
def percent(value, digits=0):
    try:
        return f"{float(value) * 100:.{int(digits)}f}%"
    except (TypeError, ValueError):
        return "—"


@register.filter
def get(mapping, key):
    try:
        return mapping.get(key, 0)
    except AttributeError:
        return 0


@register.simple_tag
def sparkline(values, width=120, height=32):
    """Inline SVG sparkline; no JavaScript needed for the list views."""
    values = list(values or [])
    if len(values) < 2:
        return ""
    lo, hi = 0, 100
    step = width / (len(values) - 1)
    points = [(i * step, height - 3 - (v - lo) / (hi - lo) * (height - 6)) for i, v in enumerate(values)]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(points))
    last_x, last_y = points[-1]
    return mark_safe(
        f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" aria-hidden="true">'
        f'<path d="{path}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.5" fill="currentColor"/></svg>'
    )


@register.simple_tag
def ring(score, size=132, stroke=10):
    """Circular score gauge."""
    try:
        score = max(0, min(100, int(score)))
    except (TypeError, ValueError):
        score = 0
    r = (size - stroke) / 2
    circumference = 2 * 3.14159265 * r
    offset = circumference * (1 - score / 100)
    c = size / 2
    return mark_safe(
        f'<svg class="ring {tone(score)}" viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img" aria-label="Health {score} out of 100">'
        f'<circle cx="{c}" cy="{c}" r="{r}" class="ring-track" stroke-width="{stroke}" fill="none"/>'
        f'<circle cx="{c}" cy="{c}" r="{r}" class="ring-value" stroke-width="{stroke}" fill="none" '
        f'stroke-dasharray="{circumference:.1f}" stroke-dashoffset="{offset:.1f}" stroke-linecap="round" '
        f'transform="rotate(-90 {c} {c})"/></svg>'
    )
