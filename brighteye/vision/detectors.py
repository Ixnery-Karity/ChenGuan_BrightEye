"""真实摄像头视觉检测后端（MediaPipe Tasks API + OpenCV）。

设计原则：重依赖（cv2 / mediapipe）全部延迟导入，
若环境缺少依赖或模型文件，`RealVisionBackend.available()` 返回 False，
上层 monitor 自动回退到模拟器，保证 Demo 在任何机器都能运行。

本后端基于 MediaPipe 新版 Tasks API（0.10+）：
  - FaceLandmarker（478 点）→ EAR 眨眼检测 + 瞳距测距
  - PoseLandmarker（33 点） → 颅椎角(CVA)近似 + 高低肩

模型文件存放于 brighteye/assets/models/：
  - face_landmarker.task
  - pose_landmarker_lite.task
"""

from __future__ import annotations

import math
import os

from ..core.metrics import FrameSample

# FaceMesh/FaceLandmarker 左右眼关键点（6 点 EAR，索引与旧版一致）
_RIGHT_EYE = [33, 160, 158, 133, 153, 144]
_LEFT_EYE = [362, 385, 387, 263, 373, 380]

# 虹膜中心关键点（478 点模型自带的精修虹膜，468=右眼虹膜中心、473=左眼虹膜中心）。
# 用虹膜中心连线作为"真实瞳距"的像素度量，远比外眼角连线贴近解剖学瞳距。
_RIGHT_IRIS = 468
_LEFT_IRIS = 473
# 成人平均瞳距(mm)。虹膜中心连线≈瞳孔间距，与该常数量纲一致，避免系统性偏差。
_AVG_IPD_MM = 63.0
# 内眦(133/362)间距回退方案使用的内眦间距(mm)，当模型未输出虹膜点时启用。
_INNER_CANTHAL_MM = 32.0

# Pose 关键点索引（与 BlazePose 33 点定义一致）
_POSE_LEFT_EAR = 7
_POSE_RIGHT_EAR = 8
_POSE_LEFT_SHOULDER = 11
_POSE_RIGHT_SHOULDER = 12

_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "models")
_FACE_MODEL = os.path.join(_MODELS_DIR, "face_landmarker.task")
_POSE_MODEL = os.path.join(_MODELS_DIR, "pose_landmarker_lite.task")


def _euclid(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _eye_aspect_ratio(pts) -> float:
    # EAR = (|p2-p6| + |p3-p5|) / (2 * |p1-p4|)
    p1, p2, p3, p4, p5, p6 = pts
    vert = _euclid(p2, p6) + _euclid(p3, p5)
    horiz = 2.0 * _euclid(p1, p4)
    return vert / horiz if horiz else 0.0


def _focal_px_from_fov(width_px: int, hfov_deg: float) -> float:
    """由画面宽度与水平视场角推导针孔焦距(像素)，使测距与分辨率无关。

    focal = (W/2) / tan(HFOV/2)；典型网络摄像头 HFOV≈60°。
    """
    half = math.radians(max(1.0, min(170.0, hfov_deg)) / 2.0)
    return (width_px / 2.0) / math.tan(half)


def _cva_same_side(ear_lm, sh_lm, w: int, h: int) -> float:
    """颅椎角近似：同侧 肩关节 → 耳屏 连线与水平线的夹角(度)。

    采用同侧拓扑（左耳配左肩、右耳配右肩），而非双肩中点近似 C7：
    2D 投影下，身体哪怕轻微侧转，双肩中点也会沿水平方向剧烈偏移，
    使 dx 虚增、角度骤降而误报"前倾"；同侧耳-肩连线随躯干一起旋转，
    对侧转显著更鲁棒（Gemini 交叉评审建议，已实测验证）。
    以像素坐标计算(分别乘 w、h 还原宽高比)，结果夹在 [0,90]。
    """
    dx = abs((ear_lm.x - sh_lm.x) * w)
    dy = (sh_lm.y - ear_lm.y) * h  # 图像 y 向下，正常坐姿肩在耳下方→为正
    ang = abs(math.degrees(math.atan2(dy, dx + 1e-6)))
    return max(0.0, min(90.0, ang))


def _visibility(lm) -> float:
    v = getattr(lm, "visibility", None)
    return float(v) if v is not None else 1.0


class RealVisionBackend:
    """封装 MediaPipe Tasks 推理。仅在依赖与模型文件可用时实例化。"""

    def __init__(self):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        self._mp = mp
        self._vision = vision

        face_opts = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=_FACE_MODEL),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            output_face_blendshapes=True,   # 开启 52 维表情系数 → 情绪分析(零新增模型)
        )
        self.face_landmarker = vision.FaceLandmarker.create_from_options(face_opts)

        pose_opts = vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=_POSE_MODEL),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=1,
        )
        self.pose_landmarker = vision.PoseLandmarker.create_from_options(pose_opts)

        self._cva_ema: float | None = None  # 颅椎角时间平滑状态

    @staticmethod
    def available() -> bool:
        try:
            import cv2  # noqa: F401
            import mediapipe  # noqa: F401
            from mediapipe.tasks.python import vision  # noqa: F401
        except Exception:
            return False
        return os.path.isfile(_FACE_MODEL) and os.path.isfile(_POSE_MODEL)

    def process(self, frame_bgr, thresholds, timestamp: float) -> FrameSample:
        import cv2
        import mediapipe as mp

        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        ts_ms = int(timestamp * 1000)

        sample = FrameSample(timestamp=timestamp, face_present=False)

        # ---- 眼部 / 距离 ----
        fr = self.face_landmarker.detect_for_video(mp_image, ts_ms)
        if fr.face_landmarks:
            sample.face_present = True
            lm = fr.face_landmarks[0]
            pts = [(p.x * w, p.y * h) for p in lm]
            ear = (
                _eye_aspect_ratio([pts[i] for i in _RIGHT_EYE])
                + _eye_aspect_ratio([pts[i] for i in _LEFT_EYE])
            ) / 2.0
            sample.ear = ear

            # 用眼距离：distance = focal × 真实瞳距 / 像素瞳距
            #  - 焦距按画面宽度与视场角推导，分辨率无关；
            #  - 优先用虹膜中心连线作"真实瞳距"(63mm)，回退到内眦间距(32mm)。
            focal = _focal_px_from_fov(w, getattr(thresholds, "camera_hfov_deg", 60.0))
            if len(pts) > _LEFT_IRIS:
                ipd_px = _euclid(pts[_RIGHT_IRIS], pts[_LEFT_IRIS])
                real_mm = _AVG_IPD_MM
            else:
                ipd_px = _euclid(pts[133], pts[362])
                real_mm = _INNER_CANTHAL_MM
            if ipd_px > 1:
                sample.distance_cm = (focal * real_mm / ipd_px) / 10.0

            # ---- 表情系数(blendshapes) → 供情绪分析 ----
            bs = getattr(fr, "face_blendshapes", None)
            if bs:
                try:
                    sample.blendshapes = {c.category_name: float(c.score) for c in bs[0]}
                except Exception:
                    sample.blendshapes = None

        # ---- 体态 ----
        pr = self.pose_landmarker.detect_for_video(mp_image, ts_ms)
        if pr.pose_landmarks:
            pl = pr.pose_landmarks[0]
            ear_l, ear_r = pl[_POSE_LEFT_EAR], pl[_POSE_RIGHT_EAR]
            sh_l, sh_r = pl[_POSE_LEFT_SHOULDER], pl[_POSE_RIGHT_SHOULDER]

            # 同侧拓扑：左耳配左肩、右耳配右肩各算一次 CVA，
            # 仅纳入"耳+同侧肩"均足够可见的一侧并按可见度加权；
            # 单侧被头发/手/侧脸遮挡时自动剔除该侧，身体侧转时不再误报前倾。
            vis_floor = getattr(thresholds, "cva_vis_floor", 0.30)
            est = []  # (cva, weight)
            for ear_lm, sh_lm in ((ear_l, sh_l), (ear_r, sh_r)):
                vw = min(_visibility(ear_lm), _visibility(sh_lm))
                if vw >= vis_floor:
                    est.append((_cva_same_side(ear_lm, sh_lm, w, h), vw))
            if not est:  # 两侧都不可靠 → 放宽，仍给一个尽力估计
                est = [(_cva_same_side(ear_l, sh_l, w, h), max(_visibility(ear_l), 1e-3)),
                       (_cva_same_side(ear_r, sh_r, w, h), max(_visibility(ear_r), 1e-3))]
            wsum = sum(vw for _, vw in est)
            cva_raw = sum(a * vw for a, vw in est) / wsum

            # 时间指数平滑(EMA)：抑制逐帧关键点抖动，读数更稳更准
            alpha = getattr(thresholds, "cva_smooth", 0.25)
            self._cva_ema = cva_raw if self._cva_ema is None \
                else alpha * cva_raw + (1.0 - alpha) * self._cva_ema
            sample.cva = self._cva_ema

            # 高低肩：双肩连线与水平的夹角
            sdx = (sh_r.x - sh_l.x) * w
            sdy = (sh_r.y - sh_l.y) * h
            sample.shoulder_tilt = abs(math.degrees(math.atan2(sdy, abs(sdx) + 1e-6)))

        return sample

    def close(self) -> None:
        for obj in (getattr(self, "face_landmarker", None), getattr(self, "pose_landmarker", None)):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
