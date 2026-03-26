import json
from pathlib import Path

from subtitld.modules.session import PATH_SUBTITLD_USER_CONFIG_FILE


class Config(dict):
    def __init__(self, filepath=PATH_SUBTITLD_USER_CONFIG_FILE):
        self.filepath = Path(filepath)
        
        if self.filepath.exists():
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except json.JSONDecodeError:
                data = json.loads('{}')
        else:
            data = json.loads('{}')

        self.load_defaults()

        super().__init__(data)
        
    def __getitem__(self, key):
        return super().get(key, False)  # returns False if not found
    
    def save(self):
        with open(self.filepath, 'w', encoding='utf-8') as f:
            json.dump(self, f, indent=4)

    def load_defaults(self):
        self.setdefault('timeline_zoom', 100.0)
        self.setdefault('playback_speed', 1.0)
        self.setdefault('repeat_activated', False)
        self.setdefault('playback_repeat_duration', 10.0)
        self.setdefault('playback_repeat_times', 3)
        self.setdefault('timeline', {})
        self.setdefault('interface_splitters', {})
        self.setdefault('shortcuts', {})
        self.setdefault('default_new_subtitle_duration', 10.0)
        self.setdefault('new_subtitle_start_from_last', False)
        self.setdefault('new_subtitle_and_play', False)
        self.setdefault('new_subtitle_to_next_start', False)
        self.setdefault('quality_check', {})
        self.setdefault('default_values', {})
        self.setdefault('videoplayer', {})
        self.setdefault('export', {})
        self.setdefault('autosave', {})
        self.setdefault('transcription', {})
        self.setdefault('translation', {})
        self.setdefault('dubbing', {})

    @staticmethod
    def get_valid_keys():
        return {
            'timeline_zoom',
            'playback_speed',
            'repeat_activated',
            'playback_repeat_duration',
            'playback_repeat_times',
            'timeline',
            'interface_splitters',
            'shortcuts',
            'default_new_subtitle_duration',
            'new_subtitle_start_from_last',
            'new_subtitle_and_play',
            'new_subtitle_to_next_start',
            'quality_check',
            'default_values',
            'videoplayer',
            'export',
            'autosave',
            'transcription',
            'translation',
            'dubbing',
        }

    @staticmethod
    def validate_config_keys(config_data):
        valid_keys = Config.get_valid_keys()
        invalid_keys = set(config_data.keys()) - valid_keys
        return len(invalid_keys) == 0, invalid_keys

        