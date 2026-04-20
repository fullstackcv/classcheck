"""Enroll one person via webcam.

Captures N frames, picks the frames where a face was detected, enrolls the
person in facestack's DB (for embeddings), and creates a ClassCheck Person
row linked to the facestack person_id via `facestack_person_id`.

Usage:
    classcheck-enroll --name "Alice" --role student
    classcheck-enroll --name "Mr Sharma" --role teacher --frames 10
"""

import argparse
import logging
import sys
import time
from typing import Optional

import cv2

from facestack.pipeline import FaceStackPipeline

from classcheck.models import Person, Role, get_session, init_db
from classcheck.paths import build_facestack_config

logger = logging.getLogger("classcheck.enroll")


def capture_enrollment_frames(
    detector,
    camera_source: str = "0",
    n_frames: int = 5,
    min_gap_s: float = 0.8,
    hard_timeout_s: float = 60.0,
    show_preview: bool = True,
) -> list:
    """Watch the camera and capture n_frames where a face IS detected.

    Runs the given detector on each camera frame. A frame is "captured"
    only if exactly one face is found in it. Between captures we wait
    at least `min_gap_s` so we don't take 5 near-identical shots.

    Aborts after `hard_timeout_s` regardless of outcome.
    """
    src = int(camera_source) if camera_source.isdigit() else camera_source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera: {camera_source}")

    captured: list = []
    try:
        t_start = time.monotonic()
        last_capture_t = 0.0
        while len(captured) < n_frames:
            if time.monotonic() - t_start > hard_timeout_s:
                logger.warning(
                    "Enrollment timed out after %.0fs with %d/%d frames captured",
                    hard_timeout_s, len(captured), n_frames,
                )
                break

            ok, frame = cap.read()
            if not ok or frame is None:
                continue

            # Run detection — deciding whether this frame is keepable
            dets = detector.detect(frame)
            n_faces = len(dets)
            face_ok = (n_faces == 1)
            may_capture = face_ok and (time.monotonic() - last_capture_t >= min_gap_s)

            if may_capture:
                captured.append(frame)
                last_capture_t = time.monotonic()
                logger.info("Captured frame %d/%d", len(captured), n_frames)

            if show_preview:
                preview = frame.copy()
                # Draw detections
                color = (0, 255, 0) if face_ok else (0, 200, 255)
                for d in dets:
                    x1, y1, x2, y2 = d.bbox
                    cv2.rectangle(preview, (x1, y1), (x2, y2), color, 2)

                if n_faces == 0:
                    status = "Looking for your face..."
                elif n_faces > 1:
                    status = f"Too many faces in frame ({n_faces}). Only one person please."
                else:
                    status = f"Captured {len(captured)}/{n_frames} — hold still"

                cv2.putText(
                    preview, status, (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2,
                )
                cv2.imshow("ClassCheck Enrollment", preview)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    raise KeyboardInterrupt
    finally:
        cap.release()
        if show_preview:
            cv2.destroyAllWindows()

    return captured


def enroll_person(
    name: str,
    role: Role,
    frames: list,
    pipeline: FaceStackPipeline,
    db_url: str,
    email: Optional[str] = None,
) -> Person:
    """Run facestack enrollment in APPEND mode, then mirror to ClassCheck.

    If a facestack Person with this name already exists, we pull their
    existing embeddings, compute new ones from the new frames, and
    re-enroll with the combined list. This means repeated enrollment
    keeps accumulating reference embeddings for that person instead of
    overriding them (better recognition over time, no errors on re-enroll).
    """
    import numpy as np

    from facestack.database.models import FaceEmbedding, Person as FsPerson

    # 1. Compute new embeddings for the new frames (detect → align → embed).
    new_embeddings = []
    for f in frames:
        dets = pipeline._detector.detect(f)
        if not dets:
            continue
        best = max(dets, key=lambda d: d.confidence)
        try:
            aligned = pipeline._aligner.align(f, landmarks=best.landmarks)
        except Exception as e:
            logger.debug("Align failed (%s), falling back to bbox crop", e)
            x1, y1, x2, y2 = best.bbox
            h, w = f.shape[:2]
            crop = f[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
            tw, th = pipeline.config.align_target_size
            aligned = cv2.resize(crop, (tw, th))
        emb = pipeline._recognizer.get_embedding(aligned)
        new_embeddings.append(emb)

    if not new_embeddings:
        raise RuntimeError(
            "No faces extractable from the captured frames. "
            "Try again in better lighting."
        )

    # 2. If this person already exists in facestack, pull their prior embeddings
    #    (for the SAME recognizer) and combine — so we append, not override.
    model_name = pipeline._recognizer.name
    fs_session = pipeline._session
    existing = fs_session.query(FsPerson).filter(FsPerson.name == name).first()

    combined = list(new_embeddings)
    if existing is not None:
        prior = [
            np.frombuffer(fe.embedding, dtype=np.float32)
            for fe in existing.embeddings
            if fe.model_name == model_name
        ]
        combined = prior + combined
        logger.info(
            "Appending %d new embeddings to %d existing for %r (total=%d)",
            len(new_embeddings), len(prior), name, len(combined),
        )
    else:
        logger.info("First-time enrollment of %r with %d embeddings", name, len(combined))

    # 3. Call facestack's lower-level enroll_person with the combined set.
    #    (pipeline.enroll() always replaces; we need explicit control here.)
    fs_person = pipeline._enrollment.enroll_person(
        name=name,
        embeddings=combined,
        model_name=model_name,
    )
    pipeline._name_cache[fs_person.id] = fs_person.name

    # Create a ClassCheck Person row with the role.
    init_db(db_url)
    cc_session = get_session(db_url)
    try:
        existing = cc_session.query(Person).filter(Person.facestack_person_id == fs_person.id).first()
        if existing is not None:
            logger.warning("ClassCheck Person row already exists for %s — updating role/email.", name)
            existing.role = role.value
            if email is not None:
                existing.email = email
            cc_session.commit()
            return existing

        cc_person = Person(
            name=name,
            role=role.value,
            email=email,
            facestack_person_id=fs_person.id,
        )
        cc_session.add(cc_person)
        cc_session.commit()
        cc_session.refresh(cc_person)
        return cc_person
    finally:
        cc_session.close()


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description="Enroll a person via webcam into ClassCheck.")
    parser.add_argument("--name", required=True, help="Display name")
    parser.add_argument(
        "--role",
        required=True,
        choices=[r.value for r in Role],
        help="Role: student, teacher, or admin",
    )
    parser.add_argument("--email", default=None, help="Contact email for reports")
    parser.add_argument("--camera", default="0", help='Camera source (default "0")')
    parser.add_argument(
        "--frames", type=int, default=5,
        help="How many frames WITH A DETECTED FACE to capture",
    )
    parser.add_argument("--no-preview", action="store_true", help="Disable the preview window")
    parser.add_argument("--db", default=None, help="ClassCheck DB URL (default: ~/.classcheck/classcheck.db)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s %(message)s")

    # Build pipeline FIRST so enrollment can use the detector to gate frame capture.
    print("Loading face recognition pipeline (first run may take a few seconds)...")
    pipeline = FaceStackPipeline(config=build_facestack_config())
    try:
        print(
            f"Capturing up to {args.frames} frames for '{args.name}' ({args.role}). "
            f"Look at the camera — I'm waiting for your face to appear."
        )
        frames = capture_enrollment_frames(
            detector=pipeline._detector,
            camera_source=args.camera,
            n_frames=args.frames,
            show_preview=not args.no_preview,
        )
        if not frames:
            print(
                "No face frames captured. Either the camera didn't see you, or you "
                "interrupted. Try again in better lighting.",
                file=sys.stderr,
            )
            sys.exit(1)

        cc_person = enroll_person(
            name=args.name,
            role=Role(args.role),
            frames=frames,
            pipeline=pipeline,
            db_url=args.db,
            email=args.email,
        )
    finally:
        pipeline.close()

    print(
        f"Enrolled: id={cc_person.id} name={cc_person.name!r} role={cc_person.role!r} "
        f"facestack_person_id={cc_person.facestack_person_id}"
    )


if __name__ == "__main__":
    main()
