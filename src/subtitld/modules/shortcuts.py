"""Shortcuts module - Robust customizable shortcut system with decorators

"""

from PySide6.QtGui import QAction


class ShortcutRegistry:
    """Registry for managing shortcuts and their associated actions"""
    
    def __init__(self):
        self._shortcuts = {}
    
    def register(self, command_id, description, default_keys, handler):
        """Register a shortcut with its handler"""
        self._shortcuts[command_id] = {
            'description': description,
            'default_keys': default_keys,
            'handler': handler
        }
    
    def shortcut(self, command_id, description, keys):
        """Decorator to register a function as a shortcut handler

        `description` is the key of its text in the locale files
        (shortcuts.<command_id>); the keyboard panel shows it through _(),
        in the interface language.

        Usage:
            @shortcut('my_command', 'shortcuts.my_command', ['Ctrl+K'])
            def my_handler(self):
                # handler code
        """
        def decorator(func):
            self.register(command_id, description, keys, func)
            return func
        return decorator
    
    def get_description(self, command_id):
        return self._shortcuts.get(command_id, {}).get('description', '')
    
    def get_default_keys(self, command_id):
        return self._shortcuts.get(command_id, {}).get('default_keys', [])
    
    def get_handler(self, command_id):
        return self._shortcuts.get(command_id, {}).get('handler')
    
    def get_all_commands(self):
        return list(self._shortcuts.keys())


# Global registry
_registry = ShortcutRegistry()
shortcut = _registry.shortcut

# Legacy compatibility
shortcuts_dict = {}
default_shortcuts_dict = {}


def load(self, shortcut_commands):
    """Load shortcuts commands on widgets"""
    # Update legacy dicts
    shortcuts_dict.update({cmd: _registry.get_description(cmd) for cmd in _registry.get_all_commands()})
    default_shortcuts_dict.update({cmd: _registry.get_default_keys(cmd) for cmd in _registry.get_all_commands()})
    
    for command_id in _registry.get_all_commands():
        if command_id not in shortcut_commands:
            shortcut_commands[command_id] = _registry.get_default_keys(command_id)
    
    # Keep each command's action, so a shortcut edited in the panel can be
    # put on the live QAction instead of waiting for the next start. Without
    # this the only handle on an action is its text, and re-running load()
    # would add a second action per command rather than update it.
    self._shortcut_actions = {}

    for command_id, keys in shortcut_commands.items():
        handler = _registry.get_handler(command_id)
        if handler:
            action = QAction(_registry.get_description(command_id), self)
            action.setShortcuts([keys] if isinstance(keys, str) else list(keys))
            action.triggered.connect(lambda checked=False, h=handler: h(self))
            self.addAction(action)
            self._shortcut_actions[command_id] = action


def apply(self, command_id, keys):
    """Put `keys` on a command that is already loaded, so a shortcut changed
    in the keyboard panel works straight away.

    Returns False when the command has no action (nothing registered a
    handler for it), which is the panel's cue that a restart is needed."""
    action = getattr(self, '_shortcut_actions', {}).get(command_id)
    if action is None:
        return False
    if isinstance(keys, str):
        keys = [keys]
    action.setShortcuts([key for key in keys if key])
    return True


def disable_actions(self):
    for action in self.actions():
        action.setEnabled(False)


def enable_actions(self):
    for action in self.actions():
        action.setEnabled(True)

