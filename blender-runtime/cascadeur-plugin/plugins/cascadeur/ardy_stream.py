# Copyright (c) 2026 Chamiseul. All rights reserved.
"""ARDY generating continuously into an open Cascadeur scene.

Two clocks have to be kept apart. The model runs on a worker thread, because a
chunk takes about half a second of blocking HTTP and doing that on Cascadeur's
thread freezes the window. The scene is written from Cascadeur's own idle
event, because the scene is not thread-safe and nothing else may touch it.
The queue between them is the whole design.

Measured on an RTX 5080, 8 diffusion steps:

    generate one chunk      0.49 s      (median of five, 0.48-0.59)
    apply it to the rig     0.09 s
    ------------------------------
    total per chunk         0.58 s
    chunk plays for         2.00 s      (40 frames at 20 fps)

So generation runs about four times faster than playback, and the queue grows
rather than starves. `scene_idle` fires at 2 Hz, which is twice per chunk of
work - enough to collect them promptly without polling hard.

This is only possible because chunks are written straight onto the rig. The
route this replaced baked every chunk to FBX in a headless Blender, seconds
per chunk, and no amount of queueing hides that.

**Cascadeur's playback and scene_idle do not run together.** Press play during
a run and the chunks stop arriving until it is paused again - idle is exactly
what playback takes over. So there is no watching a take build under a moving
playhead, and no point building for it: keys land as they are generated, and
what is on screen is whatever frame the playhead is parked on. Starting
playback from here is not possible either, so it cannot be worked around from
this side: ActionManager.call_action accepts any string, known or not, and
nine plausible names for the play action all returned successfully having done
nothing.
"""

import threading
import time

import csc

from events.scene_idle.scene_idle_manager import scene_idle_manager

try:
    from . import ardy_apply, ardy_live
except ImportError:  # running from a flat install
    import ardy_apply
    import ardy_live


def _extend_boundary(scene_view, last_frame):
    """Let the timeline see the frames that were just added.

    Growing the layers is not enough: the scene keeps its own playback range,
    and it stays where it was. Measured mid-stream - 282 frames of animation
    written, boundary still reporting last_frame 100 - so playback looped
    inside the first five seconds while the take kept growing past it, which
    looks exactly like a character that has stopped moving.
    """
    try:
        boundary = scene_view.animation_boundary()
    except Exception:
        return False
    changed = False
    for attribute in ("last_frame", "last_visible_frame"):
        try:
            if getattr(boundary, attribute) < last_frame:
                setattr(boundary, attribute, last_frame)
                changed = True
        except Exception:
            continue
    return changed


class Stream:
    """One live run. Not reusable - make another for the next one."""

    def __init__(self, prompt, seed=None, steps=None, chunk_frames=None,
                 history=None, on_event=None):
        self.prompt = prompt
        self.seed = seed
        self.steps = steps
        self.chunk_frames = chunk_frames
        self.history = history
        self.on_event = on_event or (lambda text: None)

        self.stream_id = None
        self.fps = 20.0
        self.scene_view = None
        self.frames_written = 0
        self.chunks_written = 0
        self.chunks_made = 0
        self.error = None
        self.stopping = False

        self._pending = []
        self._lock = threading.Lock()
        self._thread = None
        self._subscription = None
        self._applying = False
        self._started_at = 0.0

    # -- lifecycle -------------------------------------------------------

    def start(self, scene_view):
        """Open the stream and begin. `scene_view` must already hold the rig."""
        self.scene_view = scene_view
        reply = ardy_live._http_json_request(
            f"{ardy_live.ARDY_BACKEND_URL}/stream/start", method="POST",
            payload={
                "prompt": self.prompt,
                "seed": self.seed,
                "diffusion_steps": self.steps,
                "num_frames": self.chunk_frames,
                "history_frames": self.history,
            },
            timeout=120)
        self.stream_id = reply.get("stream_id")
        if not self.stream_id:
            raise RuntimeError("The service did not open a stream.")
        self.fps = float(reply.get("fps") or 20.0)
        self._started_at = time.perf_counter()

        self._thread = threading.Thread(target=self._produce, daemon=True)
        self._thread.start()
        self._subscription = scene_idle_manager.subscribe(self._on_idle)
        self.on_event(f"Live: streaming '{self.prompt}'.")

    def stop(self, commit=True):
        """Stop asking for more, and write what was collected.

        Returns how many frames were written on the way out, so the caller
        can say so. Pass commit=False to throw the run away instead.
        """
        self.stopping = True
        if self._subscription is not None:
            try:
                scene_idle_manager.unsubscribe(self._subscription)
            except Exception:
                pass
            self._subscription = None
        if self.stream_id:
            try:
                ardy_live._http_json_request(
                    f"{ardy_live.ARDY_BACKEND_URL}/stream/stop", method="POST",
                    payload={"stream_id": self.stream_id}, timeout=15)
            except Exception:
                # The run is over either way; a service that has already
                # forgotten the stream is not a failure worth reporting.
                pass

        if commit:
            # Whatever the worker produced but idle never got to. Losing the
            # tail of a run because the last tick did not come round would be
            # a silly way to lose two seconds of motion.
            return self.commit()
        with self._lock:
            self._pending = []
        return 0

    @property
    def running(self):
        return self._subscription is not None and not self.stopping

    def snapshot(self):
        """Numbers for the panel: how far ahead generation is."""
        with self._lock:
            queued = len(self._pending)
        played = self.frames_written / self.fps if self.fps else 0.0
        return {
            "frames": self.frames_written,
            "seconds": played,
            "queued": queued,
            "lead": queued * (self.chunk_frames or 40) / (self.fps or 20.0),
            "chunks_made": self.chunks_made,
            "chunks_written": self.chunks_written,
            "error": self.error,
        }

    # -- the worker ------------------------------------------------------

    def _produce(self):
        """Ask for chunk after chunk until told to stop.

        Never touches the scene. Everything it learns goes on the queue.
        """
        while not self.stopping:
            try:
                reply = ardy_live._http_json_request(
                    f"{ardy_live.ARDY_BACKEND_URL}/stream/step", method="POST",
                    payload={"stream_id": self.stream_id}, timeout=300)
            except Exception as error:
                if not self.stopping:
                    self.error = f"{type(error).__name__}: {error}"
                return

            chunk_path = reply.get("chunk_path")
            if not chunk_path:
                self.error = ("The service returned no chunk path - it is "
                              "probably an older build.")
                return

            with self._lock:
                self._pending.append(reply)
                self.chunks_made += 1

            # Enough is enough: a run left alone would fill the disk with
            # chunks nobody is watching. Twenty chunks is forty seconds of
            # motion queued ahead.
            while not self.stopping:
                with self._lock:
                    waiting = len(self._pending)
                if waiting < 20:
                    break
                time.sleep(0.2)

    # -- Cascadeur's thread ----------------------------------------------

    def _on_idle(self, scene):
        """Apply one queued chunk. Runs on Cascadeur's own idle event.

        One per tick on purpose. Idle fires twice a second and a chunk takes
        0.09 s to write, so the queue drains four times faster than it fills
        while leaving the application responsive between writes.
        """
        if self._applying or self.stopping:
            return
        with self._lock:
            reply = self._pending.pop(0) if self._pending else None
        if reply is None:
            return

        self._applying = True
        try:
            self._write(reply)
        except Exception as error:
            self.error = f"{type(error).__name__}: {error}"
            self.stop()
        finally:
            self._applying = False

    def _write(self, reply):
        domain = self.scene_view.domain_scene()
        report = ardy_apply.apply_file(domain, reply["chunk_path"],
                                       frame_start=self.frames_written)
        if not report["ok"]:
            raise RuntimeError("A chunk could not be written to the rig.")
        self.frames_written += report["frames"]
        self.chunks_written += 1
        _extend_boundary(self.scene_view, self.frames_written)

    def commit(self):
        """Write everything collected onto the rig. Main thread only.

        About 0.09s per chunk, so a minute of streamed motion lands in under
        three seconds - worth waiting once at the end rather than paying it
        thirty times during the run.
        """
        with self._lock:
            queued, self._pending = list(self._pending), []
        collected = queued
        if not collected:
            return 0

        written = 0
        for reply in collected:
            try:
                self._write(reply)
                written += int(reply.get("frames") or 0)
            except Exception as error:
                self.error = f"{type(error).__name__}: {error}"
                break
        return written


_current = None


def current():
    return _current


def start(prompt, scene_view, **kwargs):
    """Begin a live run, replacing any that was going."""
    global _current
    stop()
    stream = Stream(prompt, **kwargs)
    stream.start(scene_view)
    _current = stream
    return stream


def stop(commit=True):
    """End the run. Returns how many frames it wrote on the way out."""
    global _current
    written = 0
    if _current is not None:
        written = _current.stop(commit=commit)
        _current = None
    return written
