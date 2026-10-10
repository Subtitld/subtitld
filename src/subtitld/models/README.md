# Models

## blaze_face_full_range_sparse.tflite

MediaPipe's BlazeFace Sparse (full-range) face detector, which the Speakers
panel runs through MediaPipe's Tasks API (`modules/face_detection.py`) to pick
a portrait for each speaker.

- Source: <https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_full_range/float16/latest/blaze_face_full_range_sparse.tflite>
- SHA-256: `2c3728e6da56f21e21a320433396fb06d40d9088f2247c05e5635a688d45dfe1`
  (676,746 bytes), the same file as
  `mediapipe/modules/face_detection/face_detection_full_range_sparse.tflite`
  in the MediaPipe 0.10.21 wheel
- Model card: <https://storage.googleapis.com/mediapipe-assets/MediaPipe%20BlazeFace%20Sparse%20Model%20Card%20(Full%20Range).pdf>
- By Google's MediaPipe team, under the Apache License 2.0 (`LICENSE` here)

The Tasks API runs it from MediaPipe 0.10.33 on. The short-range model, which
every Tasks release can run, is made for faces within 2 m of the camera (this
one, 5 m); on film footage it missed the medium shots and found faces in
rocket nozzles.
