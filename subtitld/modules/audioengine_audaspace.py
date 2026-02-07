import aud

class AudaspaceAudioDevice:
    def __init__(self):
        self.device = aud.Device()
        self.sequence = aud.Sequence()
        self.handle = self.device.play(self.sequence)
        self.background_sound = None
        self.background = None
        self.vocals_sound = None
        self.vocals = None

    def load(self, filepath):
        self.background_sound = aud.Sound.file(filepath)
        duration = self.background_sound.length/self.background_sound.specs[0]
        self.background = self.sequence.add(
            sound=self.background_sound,
            begin=0.0,
            end=duration,
            skip=0.0
        )

    def play(self, position=None):
        if position is not None:
            self.handle.position = position
        self.handle.resume()

    def pause(self):
        if self.handle:
            self.handle.pause()

    def stop(self):
        if self.handle:
            self.handle.stop()

    def seek(self, seconds):
        if self.handle:
            self.handle.position = seconds

    def set_volume(self, volume):
        if self.handle:
            self.handle.volume = volume

    def set_speed(self, speed):
        if self.handle:
            self.handle.pitch = speed

    def get_position(self):
        return self.handle.position if self.handle else 0.0
