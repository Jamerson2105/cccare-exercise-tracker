"""Text-to-speech on its own thread, so talking never freezes the UI.

Speech is "newest wins": a new message cuts off whatever is being said right now,
so announcements are always about what is happening now instead of piling up in a queue.

On Windows it talks to the built-in SAPI voice directly (through comtypes, which
pyttsx3 already installs). SAPI can speak asynchronously and purge the current
sentence, which pyttsx3 can't do. On other systems it falls back to pyttsx3.

Usage:
    speaker = Speaker()
    speaker.say("Set complete")
    speaker.muted = True
"""

import queue
import sys
import threading

# Speed. SAPI uses -10 (slowest) to 10 (fastest), and 0 is normal.
SAPI_RATE = 3
PYTTSX3_RATE = 215        # words per minute, used only by the pyttsx3 fallback

# SAPI SpeakFlags
SVSF_ASYNC = 1             # return straight away, speak in the background
SVSF_PURGE_BEFORE_SPEAK = 2  # stop the current sentence first

_STOP = object()           # queue marker meaning "stop talking now" (used by mute)


class Speaker:
    def __init__(self):
        self._muted = False
        self.q = queue.Queue()
        self.available = True
        self.error = None      # last speech error (checked by the self-test)
        threading.Thread(target=self._worker, daemon=True).start()

    @property
    def muted(self):
        return self._muted

    @muted.setter
    def muted(self, value):
        self._muted = bool(value)
        if self._muted:
            self.q.put(_STOP)  # cut off anything being said

    def say(self, text):
        if self._muted or not text:
            return
        self.q.put(text)
        print("[voice]", text)

    def _newest(self, item):
        """Skip anything older that is still waiting: only the newest message matters."""
        while True:
            try:
                item = self.q.get_nowait()
            except queue.Empty:
                return item

    # ---------------------------------------------------------------------
    def _worker(self):
        if sys.platform == "win32":
            try:
                self._run_sapi()
                return
            except Exception as e:
                print("Windows voice not available, trying pyttsx3:", e)
        try:
            self._run_pyttsx3()
        except Exception as e:  # no speech at all: keep the app running silently
            print("Text-to-speech not available:", e)
            self.available = False
            self.error = e

    def _run_sapi(self):
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        # dynamic=True avoids generating wrapper files, which also works inside the .exe
        voice = comtypes.client.CreateObject("SAPI.SpVoice", dynamic=True)
        voice.Rate = SAPI_RATE
        while True:
            item = self._newest(self.q.get())
            try:
                if item is _STOP or self._muted:
                    voice.Speak("", SVSF_ASYNC | SVSF_PURGE_BEFORE_SPEAK)   # silence
                else:
                    # returns immediately; interrupts the previous sentence
                    voice.Speak(item, SVSF_ASYNC | SVSF_PURGE_BEFORE_SPEAK)
            except Exception as e:
                self.error = e
                print("Text-to-speech error:", e)

    def _run_pyttsx3(self):
        import pyttsx3
        while True:
            item = self._newest(self.q.get())
            if item is _STOP or self._muted:
                continue
            try:
                # A fresh engine per message avoids a pyttsx3 bug where runAndWait()
                # hangs after the first call on a thread. This path can't interrupt
                # a sentence, but stale messages are still skipped.
                engine = pyttsx3.init()
                engine.setProperty("rate", PYTTSX3_RATE)
                engine.say(item)
                engine.runAndWait()
                engine.stop()
            except Exception as e:
                self.error = e
                print("Text-to-speech error:", e)
