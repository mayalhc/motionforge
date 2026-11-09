"""ONNX Runtime for MotionBricks SOMA, in a process of its own.

Blender 5.0 loads an older Microsoft C++ runtime than ONNX Runtime needs and
the first session in that process dies inside MSVCP140. Blender's bundled
python.exe loads the system's, so the model runs here and `mb_soma` talks to it
over pipes: a 4-byte length, then an .npz of arrays, both ways.

  python -I mb_soma_worker.py <onnxruntime folder> <model.onnx>
"""

import io
import struct
import sys


def read(stream):
    head = stream.read(4)
    if len(head) < 4:
        return None
    (size,) = struct.unpack("<I", head)
    return stream.read(size)


def write(stream, arrays):
    buffer = io.BytesIO()
    np.savez(buffer, **arrays)
    data = buffer.getvalue()
    stream.write(struct.pack("<I", len(data)) + data)
    stream.flush()


def main():
    global np
    sys.path.append(sys.argv[1])
    import numpy as np
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.log_severity_level = 3
    session = ort.InferenceSession(sys.argv[2], sess_options=options,
                                   providers=["CPUExecutionProvider"])
    names = [o.name for o in session.get_outputs()]
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    write(stdout, {"ready": np.zeros(1)})
    while True:
        data = read(stdin)
        if data is None:
            return
        with np.load(io.BytesIO(data)) as loaded:
            inputs = {key: loaded[key] for key in loaded.files}
        write(stdout, dict(zip(names, session.run(None, inputs))))


main()
