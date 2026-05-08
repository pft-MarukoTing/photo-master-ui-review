import argparse
from pathlib import Path

import cv2
import mediapipe as mp

# MediaPipe fingertip -> second knuckle (PIP) landmark index mapping
FINGERTIP_TO_SECOND_KNUCKLE = {
    4: 2,   # thumb tip -> MCP (thumb has only one knuckle before this)
    8: 6,   # index tip -> PIP
    12: 10, # middle tip -> PIP
    16: 14, # ring tip -> PIP
    20: 18, # pinky tip -> PIP
}

# Rainbow colors (BGR) for 10 fingers (color_mode="hand"):
# index 0-4: right thumb -> right pinky
# index 5-9: left thumb  -> left pinky
FINGER_COLORS_BY_HAND = [
    (0,   0,   255),  # red          - right thumb
    (0,   80,  255),  # orange-red   - right index
    (0,   165, 255),  # orange       - right middle
    (0,   255, 255),  # yellow       - right ring
    (0,   255, 0  ),  # green        - right pinky
    (255, 255, 0  ),  # cyan         - left thumb
    (255, 128, 0  ),  # azure        - left index
    (255, 0,   0  ),  # blue         - left middle
    (200, 0,   150),  # indigo       - left ring
    (255, 0,   255),  # violet       - left pinky
]

# Rainbow colors (BGR) for 5 fingers (color_mode="finger"):
# same color regardless of hand
FINGER_COLORS_BY_FINGER = [
    (0,   0,   255),  # red    - thumb
    (0,   165, 255),  # orange - index
    (0,   255, 255),  # yellow - middle
    (0,   255, 0  ),  # green  - ring
    (255, 0,   255),  # violet - pinky
]


class HandKeypointDetector:
    def __init__(self, max_num_hands: int = 2, min_detection_confidence: float = 0.3):
        self._hands = mp.solutions.hands.Hands(
            static_image_mode=True,
            max_num_hands=max_num_hands,
            min_detection_confidence=min_detection_confidence,
        )

    def close(self) -> None:
        self._hands.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def detect(self, image_bgr, flip_handedness: bool = False) -> list[tuple]:
        """Return list of (hand_landmarks, label) sorted Right-first, Left-second.

        MediaPipe assumes mirrored (selfie) input, so for regular (non-selfie) photos
        its Right/Left labels are swapped from the subject's perspective.
        Set flip_handedness=True to correct this for non-selfie images.
        """
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        results = self._hands.process(rgb)

        if not results.multi_hand_landmarks:
            return []

        _flip = {"Right": "Left", "Left": "Right"}
        hands_data = [
            (lm, _flip[results.multi_handedness[i].classification[0].label] if flip_handedness
                 else results.multi_handedness[i].classification[0].label)
            for i, lm in enumerate(results.multi_hand_landmarks)
        ]
        hands_data.sort(key=lambda x: 0 if x[1] == "Right" else 1)
        return hands_data

    def draw(self, image_bgr, hands_data: list) -> None:
        """Draw keypoints and connections onto image in-place."""
        mp_draw = mp.solutions.drawing_utils
        mp_styles = mp.solutions.drawing_styles
        for hand_landmarks, _ in hands_data:
            mp_draw.draw_landmarks(
                image_bgr,
                hand_landmarks,
                mp.solutions.hands.HAND_CONNECTIONS,
                mp_styles.get_default_hand_landmarks_style(),
                mp_styles.get_default_hand_connections_style(),
            )

    def draw_fingertip_boxes(
        self,
        image_bgr,
        hands_data: list,
        scale: float = 1.0,
        color_mode: str = "hand",
    ) -> None:
        """Draw a rainbow-colored box on each fingertip, sized by tip-to-second-knuckle distance.

        color_mode:
          "hand"   - 10 colors, right thumb->pinky then left thumb->pinky
          "finger" - 5 colors, same color per finger regardless of hand
        """
        h, w = image_bgr.shape[:2]
        thickness = max(1, round(max(h, w) / 512))

        for hand_landmarks, label in hands_data:
            hand_offset = 0 if label == "Right" else 5
            for finger_i, (tip_idx, knuckle_idx) in enumerate(FINGERTIP_TO_SECOND_KNUCKLE.items()):
                tip = hand_landmarks.landmark[tip_idx]

                knuckle = hand_landmarks.landmark[knuckle_idx]
                tip_px = (int(tip.x * w), int(tip.y * h))
                knuckle_px = (int(knuckle.x * w), int(knuckle.y * h))

                dist = ((tip_px[0] - knuckle_px[0]) ** 2 + (tip_px[1] - knuckle_px[1]) ** 2) ** 0.5
                half = int(dist * scale / 2)

                if color_mode == "finger":
                    color = FINGER_COLORS_BY_FINGER[finger_i]
                else:
                    color = FINGER_COLORS_BY_HAND[hand_offset + finger_i]

                cv2.rectangle(
                    image_bgr,
                    (tip_px[0] - half, tip_px[1] - half),
                    (tip_px[0] + half, tip_px[1] + half),
                    color,
                    thickness,
                )


def main():
    parser = argparse.ArgumentParser(description="MediaPipe hand keypoints detection")
    parser.add_argument("input", help="Input image path")
    parser.add_argument("--output", "-o", default="preview.jpg")
    parser.add_argument("--max-hands", type=int, default=2)
    parser.add_argument("--confidence", type=float, default=0.3)
    parser.add_argument("--fingertip-boxes", "-b", action="store_true", help="Draw boxes on fingertips instead of full keypoints")
    parser.add_argument("--flip-handedness", "-f", action="store_true", help="Flip Left/Right labels (use for non-selfie images)")
    parser.add_argument("--scale", type=float, default=0.55, help="Box size scale relative to tip-to-knuckle distance")
    parser.add_argument("--color-mode", "-c", choices=["hand", "finger"], default="finger")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    image = cv2.imread(str(input_path))
    if image is None:
        raise FileNotFoundError(f"Cannot read image: {input_path}")

    with HandKeypointDetector(
        max_num_hands=args.max_hands,
        min_detection_confidence=args.confidence,
    ) as detector:
        hands_data = detector.detect(image, flip_handedness=args.flip_handedness)

        if not hands_data:
            print("No hands detected.")
        else:
            labels = [label for _, label in hands_data]
            print(f"Detected {len(hands_data)} hand(s): {labels}")
            if args.fingertip_boxes:
                detector.draw_fingertip_boxes(image, hands_data, scale=args.scale, color_mode=args.color_mode)
            else:
                detector.draw(image, hands_data)

    cv2.imwrite(str(output_path), image)
    print(f"Saved to {output_path}")


if __name__ == "__main__":
    main()
