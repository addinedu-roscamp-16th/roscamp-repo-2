# bar_mentos YOLO26s-seg

- Classes: `chocolate_bar`, `mentos` (Roboflow `bar_mantos` v1, train 451 / valid 9 / test 8)
- Base: `yolo26s-seg.pt`, imgsz 640, trained 2026-11-24 on RTX 2060 (`args.yaml`)
- Valid (9 images, so treat as rough): mask mAP50 0.916, mAP50-95 0.867
- Used by `jetcobot_dual_app/scripts/multi_pick/sam_multi_real.py` (`--seg yolo`, default)
