# Shortcut System Usage Guide

## Overview
The shortcut system uses a decorator pattern for clean, declarative shortcut registration.

## Basic Usage with Decorator

```python
from subtitld.modules.shortcuts import shortcut

@shortcut('my_command', 'My Custom Command', ['Ctrl+K'])
def my_custom_handler(self):
    """Handler for my custom shortcut"""
    pass
    # Your logic here
```

## Usage without Decorator

```python
from subtitld.modules import shortcuts

def my_handler(self):
    pass

# Register manually
shortcuts._registry.register(
    'command_id',
    'Command Description', 
    ['Ctrl+Shift+X'],
    my_handler
)
```

## Examples

### Simple Action
```python
from subtitld.modules.shortcuts import shortcut

@shortcut('quick_save', 'Quick Save', ['Ctrl+S'])
def quick_save_handler(self):
    # Save logic
    pass
```

### Multiple Key Bindings
```python
@shortcut('delete_subtitle', 'Delete Subtitle', ['Delete', 'Backspace'])
def delete_handler(self):
    # Delete logic
    pass
```

### Complex Key Combinations
```python
@shortcut('advanced_edit', 'Advanced Edit Mode', ['Ctrl+Shift+E', 'Alt+E'])
def advanced_edit_handler(self):
    # Edit logic
    pass
```

## Key Features

- **Declarative**: Use `@shortcut` decorator above your function
- **Flexible**: Multiple key bindings per command
- **Discoverable**: All shortcuts registered in one place
- **Customizable**: Users can override default keys
- **Clean**: No boilerplate code needed
