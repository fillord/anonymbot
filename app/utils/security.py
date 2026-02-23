# app/utils/security.py
import io
import logging
from PIL import Image

def strip_exif_data(image_bytes: bytes) -> bytes:
    """Удаляет EXIF-метаданные из байтов изображения."""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        
        # Пересохраняем изображение без EXIF (получая только пиксели)
        data = list(image.getdata())
        image_without_exif = Image.new(image.mode, image.size)
        image_without_exif.putdata(data)
        
        output = io.BytesIO()
        # Сохраняем в оригинальном формате или по дефолту в JPEG
        fmt = image.format if image.format else 'JPEG'
        image_without_exif.save(output, format=fmt)
        
        return output.getvalue()
    except Exception as e:
        logging.error(f"Error stripping EXIF: {e}")
        # Если не получилось (например, это вообще не картинка), возвращаем оригинал
        return image_bytes