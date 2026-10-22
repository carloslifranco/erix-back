from pathlib import Path
from statistics import mean
from functools import lru_cache
from typing import Any


class OCRUnavailableError(RuntimeError):
    pass


class PaddleOCRService:
    def __init__(self) -> None:
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise OCRUnavailableError(
                "Não foi possível carregar o PaddleOCR ou suas dependências nativas. "
                f"Verifique as dependências Python e do sistema. Causa: {exc}"
            ) from exc

        self._engine = PaddleOCR(use_angle_cls=True, lang="pt", show_log=False)

    def extract(self, image_path: Path) -> tuple[str, float]:
        try:
            result: Any = self._engine.ocr(str(image_path), cls=True)
        except Exception as exc:
            raise OCRUnavailableError("O OCR não conseguiu processar o documento.") from exc

        lines: list[str] = []
        confidences: list[float] = []
        for page in result or []:
            for item in page or []:
                if len(item) >= 2 and len(item[1]) >= 2:
                    text, confidence = item[1][0], item[1][1]
                    if text:
                        lines.append(str(text))
                        confidences.append(float(confidence))

        return "\n".join(lines), mean(confidences) if confidences else 0.0


@lru_cache(maxsize=1)
def get_ocr_service() -> PaddleOCRService:
    return PaddleOCRService()
