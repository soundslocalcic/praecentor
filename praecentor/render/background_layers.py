from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from PIL import Image

from praecentor.io.song import SongConfigError


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        return (0, 0, 0)
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (0, 0, 0)


def _resample_lanczos() -> int:
    if hasattr(Image, "Resampling"):
        return Image.Resampling.LANCZOS
    return Image.LANCZOS


def resize_image_cover(
    image: Image.Image, width: int, height: int
) -> Image.Image:
    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return Image.new("RGBA", (width, height), (0, 0, 0, 255))

    scale = max(width / src_w, height / src_h)
    scaled_w = max(width, int(round(src_w * scale)))
    scaled_h = max(height, int(round(src_h * scale)))
    resized = image.resize((scaled_w, scaled_h), _resample_lanczos())

    left = max(0, (scaled_w - width) // 2)
    top = max(0, (scaled_h - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _apply_opacity(image: Image.Image, opacity: float) -> Image.Image:
    clamped = max(0.0, min(1.0, float(opacity)))
    rgba = image.convert("RGBA")
    if clamped >= 0.999:
        return rgba
    if clamped <= 0.0:
        return Image.new("RGBA", rgba.size, (0, 0, 0, 0))

    alpha = rgba.getchannel("A")
    alpha = alpha.point(lambda value: int(round(value * clamped)))
    out = rgba.copy()
    out.putalpha(alpha)
    return out


def _resolve_asset_path(raw_path: str, *, layout_path: Path) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute() and candidate.exists():
        return candidate.resolve()

    relative_to_layout = (layout_path.parent / candidate).resolve()
    if relative_to_layout.exists():
        return relative_to_layout

    relative_to_cwd = (Path.cwd() / candidate).resolve()
    if relative_to_cwd.exists():
        return relative_to_cwd

    raise SongConfigError(
        f"Background asset not found: {raw_path!r}. "
        "Checked layout-relative and cwd-relative paths."
    )


class _ImageLayer:
    def __init__(self, image: Image.Image) -> None:
        self._image = image.convert("RGBA")

    def frame(self) -> Image.Image:
        return self._image

    def close(self) -> None:
        return


class _VideoLayer:
    def __init__(
        self,
        *,
        file_path: Path,
        width: int,
        height: int,
        fps: int,
        opacity: float,
        loop: bool,
        ffmpeg_path: str,
        hwaccel: str | None,
    ) -> None:
        self._width = width
        self._height = height
        self._frame_size = width * height * 4
        self._opacity = opacity
        self._last_frame: Image.Image | None = None

        filter_graph = (
            f"fps={max(1, int(fps))},"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )

        cmd = [str(ffmpeg_path), "-v", "error"]
        if hwaccel is not None and hwaccel.strip():
            cmd.extend(["-hwaccel", hwaccel.strip()])
        if loop:
            cmd.extend(["-stream_loop", "-1"])
        cmd.extend(
            [
                "-i",
                str(file_path),
                "-vf",
                filter_graph,
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgba",
                "-",
            ]
        )

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise SongConfigError(
                f"Failed to start ffmpeg for background video {file_path}: {exc}"
            ) from exc

    def _read_exact(self, size: int) -> bytes | None:
        if self._proc.stdout is None:
            return None

        data = bytearray()
        while len(data) < size:
            chunk = self._proc.stdout.read(size - len(data))
            if not chunk:
                break
            data.extend(chunk)

        if len(data) != size:
            return None
        return bytes(data)

    def frame(self) -> Image.Image:
        raw = self._read_exact(self._frame_size)
        if raw is None:
            if self._last_frame is not None:
                return _apply_opacity(self._last_frame, self._opacity)
            blank = Image.new(
                "RGBA", (self._width, self._height), (0, 0, 0, 255)
            )
            self._last_frame = blank
            return _apply_opacity(blank, self._opacity)

        rgba = Image.frombytes("RGBA", (self._width, self._height), raw)
        self._last_frame = rgba
        return _apply_opacity(rgba, self._opacity)

    def close(self) -> None:
        proc = self._proc
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)

        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()


class BackgroundLayerRenderer:
    def __init__(
        self,
        *,
        width: int,
        height: int,
        base_color: tuple[int, int, int],
        layers: list[_ImageLayer | _VideoLayer],
    ) -> None:
        self._width = width
        self._height = height
        self._base_color = base_color
        self._layers = layers

    def next_frame(self) -> Image.Image:
        composed = Image.new(
            "RGBA",
            (self._width, self._height),
            (
                self._base_color[0],
                self._base_color[1],
                self._base_color[2],
                255,
            ),
        )
        for layer in self._layers:
            composed.alpha_composite(layer.frame())
        return composed.convert("RGB")

    def close(self) -> None:
        for layer in self._layers:
            layer.close()


def build_background_layer_renderer(
    *,
    background_config: dict[str, Any],
    layout_path: Path,
    width: int,
    height: int,
    fps: int,
    ffmpeg_path: str,
    hwaccel: str | None = None,
) -> BackgroundLayerRenderer | None:
    bg_type = str(background_config.get("type", "solid")).strip().lower()
    base_color = _hex_to_rgb(str(background_config.get("color", "#000000")))

    if bg_type == "solid":
        return None

    if bg_type == "layers":
        raw_layers = background_config.get("layers")
        if not isinstance(raw_layers, list):
            raise SongConfigError(
                "layout.video.background.layers must be a list when type=layers"
            )
        layer_specs = raw_layers
    elif bg_type in {"image", "video"}:
        layer_specs = [background_config]
    else:
        raise SongConfigError(
            "layout.video.background.type must be one of: solid, image, video, layers"
        )

    layers: list[_ImageLayer | _VideoLayer] = []
    for idx, layer_spec in enumerate(layer_specs):
        if not isinstance(layer_spec, dict):
            raise SongConfigError(
                f"layout.video.background.layers[{idx}] must be a mapping"
            )

        layer_type = str(layer_spec.get("type", "")).strip().lower()
        file_raw = layer_spec.get("file")
        if not isinstance(file_raw, str) or not file_raw.strip():
            raise SongConfigError(
                f"layout.video.background layer {idx} must define a "
                "non-empty string file"
            )

        opacity_raw = layer_spec.get("opacity", 1.0)
        try:
            opacity = float(opacity_raw)
        except (TypeError, ValueError):
            raise SongConfigError(
                f"layout.video.background.layers[{idx}].opacity must be numeric"
            ) from None

        resolved_path = _resolve_asset_path(
            file_raw.strip(), layout_path=layout_path
        )

        if layer_type == "image":
            try:
                with Image.open(resolved_path) as opened:
                    layer_image = resize_image_cover(
                        opened.convert("RGBA"), width, height
                    )
            except OSError as exc:
                raise SongConfigError(
                    f"Unable to open background image {resolved_path}: {exc}"
                ) from exc
            layers.append(_ImageLayer(_apply_opacity(layer_image, opacity)))
        elif layer_type == "video":
            loop = bool(layer_spec.get("loop", False))
            layers.append(
                _VideoLayer(
                    file_path=resolved_path,
                    width=width,
                    height=height,
                    fps=fps,
                    opacity=opacity,
                    loop=loop,
                    ffmpeg_path=ffmpeg_path,
                    hwaccel=hwaccel,
                )
            )
        else:
            raise SongConfigError(
                f"layout.video.background.layers[{idx}].type must be image or video"
            )

    if not layers:
        return None

    return BackgroundLayerRenderer(
        width=width,
        height=height,
        base_color=base_color,
        layers=layers,
    )
