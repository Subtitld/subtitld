from PySide6.QtCore import QPoint

from subtitld.interface.translation import _


def animate_element(animation, duration=1000, effect='fadein'):
    widget = animation.targetObject()
    animation.setDuration(duration)
    if effect.startswith('slide_'):
        original_position = widget.pos()
    if effect == 'slide_from_left':
        animation.setStartValue(QPoint(-widget.parent().width(), 0))
        animation.setEndValue(original_position)
    elif effect == 'slide_from_right':
        animation.setStartValue(QPoint(widget.parent().width(), 0))
        animation.setEndValue(original_position)
    elif effect == 'slide_from_bottom':
        animation.setStartValue(QPoint(0, widget.parent().height()))
        animation.setEndValue(original_position)
    elif effect == 'slide_from_top':
        animation.setStartValue(QPoint(0, -widget.parent().height()))
        animation.setEndValue(original_position)
    elif effect == 'slide_to_bottom':
        animation.setStartValue(original_position)
        animation.setEndValue(QPoint(0, widget.parent().height()))
    elif effect == 'fadein':
        animation.setStartValue(widget.opacity() if hasattr(widget, 'opacity') else 0)
        animation.setEndValue(1)
    elif effect == 'fadeout':
        animation.setStartValue(widget.opacity() if hasattr(widget, 'opacity') else 1)
        animation.setEndValue(0)
    animation.start()

from datetime import datetime, timedelta

def friendly_time(dt):
    """
    Returns a human-friendly string showing the time difference between `dt` and now,
    using `_()` for translatable strings.
    """
    now = datetime.now()
    
    # Convert timestamp to datetime
    if isinstance(dt, (int, float)):
        dt = datetime.fromtimestamp(dt)
    
    delta = now - dt
    seconds = int(delta.total_seconds())
    
    if seconds < 0:  # Future
        seconds = abs(seconds)
        if seconds < 60:
            return _("in a few seconds") if seconds < 5 else _("in {seconds} seconds").format(seconds=seconds)
        elif seconds < 3600:
            minutes = seconds // 60
            return _("in {minutes} minute{plural}").format(minutes=minutes, plural='' if minutes == 1 else 's')
        elif seconds < 86400:
            hours = seconds // 3600
            return _("in {hours} hour{plural}").format(hours=hours, plural='' if hours == 1 else 's')
        elif seconds < 604800:
            days = seconds // 86400
            return _("in {days} day{plural}").format(days=days, plural='' if days == 1 else 's')
        elif seconds < 2419200:
            weeks = seconds // 604800
            return _("in {weeks} week{plural}").format(weeks=weeks, plural='' if weeks == 1 else 's')
        else:
            months = seconds // 2419200
            return _("in {months} month{plural}").format(months=months, plural='' if months == 1 else 's')
    else:  # Past
        if seconds < 60:
            return _("just now") if seconds < 5 else _("{seconds} seconds ago").format(seconds=seconds)
        elif seconds < 3600:
            minutes = seconds // 60
            return _("{minutes} minute{plural} ago").format(minutes=minutes, plural='' if minutes == 1 else 's')
        elif seconds < 86400:
            hours = seconds // 3600
            return _("{hours} hour{plural} ago").format(hours=hours, plural='' if hours == 1 else 's')
        elif seconds < 604800:
            days = seconds // 86400
            return _("{days} day{plural} ago").format(days=days, plural='' if days == 1 else 's')
        elif seconds < 2419200:
            weeks = seconds // 604800
            return _("{weeks} week{plural} ago").format(weeks=weeks, plural='' if weeks == 1 else 's')
        else:
            months = seconds // 2419200
            return _("{months} month{plural} ago").format(months=months, plural='' if months == 1 else 's')

