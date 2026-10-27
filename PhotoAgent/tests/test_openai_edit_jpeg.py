import base64
import io
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageOps, ImageStat
import pillow_heif
import image_tools
import openai_client


def picture():
    image = Image.new('RGB', (96, 64), 'red')
    image.paste('blue', (48, 0, 96, 32))
    image.paste('green', (0, 32, 48, 64))
    image.paste('yellow', (48, 32, 96, 64))
    return image


def encoded(image, fmt, orientation=None):
    out = io.BytesIO()
    options = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        if fmt == 'HEIF':
            # HEIF's Pillow writer reads orientation from image metadata
            # while baking pixels, before it applies save-time EXIF kwargs.
            image.getexif()[274] = orientation
        else:
            options['exif'] = exif
    image.save(out, format=fmt, **options)
    return out.getvalue()


class OpenAIJpegTests(unittest.TestCase):
    def assert_upright(self, source, expected):
        data = image_tools.prepare_openai_edit_jpeg(source)
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.format, 'JPEG')
            self.assertEqual(result.mode, 'RGB')
            self.assertEqual(result.size, expected.size)
            self.assertIn(result.getexif().get(274, 1), (1,))
            # Compare orientation AND pixels, including mirrored cases.
            error = ImageStat.Stat(ImageChops.difference(result, expected.convert('RGB'))).mean
            self.assertLess(max(error), 5)
        return data

    def test_upright_jpeg_is_byte_identical(self):
        for orientation in (None, 1):
            with self.subTest(orientation=orientation):
                source = encoded(picture(), 'JPEG', orientation)
                self.assertIs(image_tools.prepare_openai_edit_jpeg(source), source)

    def test_all_exif_rotations_and_mirrors(self):
        transforms = {
            2: Image.Transpose.FLIP_LEFT_RIGHT,
            3: Image.Transpose.ROTATE_180,
            4: Image.Transpose.FLIP_TOP_BOTTOM,
            5: Image.Transpose.TRANSPOSE,
            6: Image.Transpose.ROTATE_270,
            7: Image.Transpose.TRANSVERSE,
            8: Image.Transpose.ROTATE_90,
        }
        for orientation, transform in transforms.items():
            with self.subTest(orientation=orientation):
                source = encoded(picture(), 'JPEG', orientation)
                original = Image.open(io.BytesIO(source))
                expected = original.transpose(transform)
                self.assert_upright(source, expected)

    def test_png_webp_and_heic_to_jpeg(self):
        for fmt in ('PNG', 'WEBP', 'HEIF'):
            with self.subTest(fmt=fmt):
                source = encoded(picture(), fmt)
                expected = ImageOps.exif_transpose(Image.open(io.BytesIO(source))).convert('RGB')
                self.assert_upright(source, expected)

    def test_heic_rotation_is_not_applied_twice(self):
        for orientation in range(2, 9):
            with self.subTest(orientation=orientation):
                source = encoded(picture(), 'HEIF', orientation)
                jpeg_reference = encoded(picture(), 'JPEG', orientation)
                expected = ImageOps.exif_transpose(Image.open(io.BytesIO(jpeg_reference)))
                self.assert_upright(source, expected)

    def test_transparency_is_white(self):
        source = encoded(Image.new('RGBA', (32, 32), (0, 0, 0, 0)), 'PNG')
        self.assert_upright(source, Image.new('RGB', (32, 32), 'white'))

    def test_cmyk_becomes_rgb_jpeg(self):
        source = encoded(picture().convert('CMYK'), 'JPEG')
        expected = Image.open(io.BytesIO(source)).convert('RGB')
        self.assert_upright(source, expected)

    def test_new_encodes_use_max_quality_and_no_chroma_subsampling(self):
        data = image_tools.prepare_openai_edit_jpeg(encoded(picture(), 'PNG'))
        with Image.open(io.BytesIO(data)) as result:
            self.assertTrue(all(value == 1 for table in result.quantization.values() for value in table))
            self.assertTrue(all(layer[1:3] == (1, 1) for layer in result.layer))

    def test_main_and_eye_repair_send_jpeg(self):
        source = encoded(picture(), 'PNG')
        result_b64 = base64.b64encode(source).decode()
        response = {'data': [{'b64_json': result_b64}], 'usage': {}}
        with patch.object(openai_client, '_post_image_edit', return_value=response) as post:
            openai_client.edit_photo(source, {'recommended_aspect_ratio': 'original'}, single_pass_edit=True)
            sent = post.call_args.args[-1]
            self.assertEqual(Image.open(io.BytesIO(sent)).format, 'JPEG')
            openai_client.edit_eye_repair_pass(source, 'Keep the eyes natural.')
            sent = post.call_args.args[-1]
            self.assertEqual(Image.open(io.BytesIO(sent)).format, 'JPEG')

    def test_http_filename_and_mime_match_jpeg(self):
        source = image_tools.prepare_openai_edit_jpeg(encoded(picture(), 'PNG'))
        with patch.object(openai_client.requests, 'post') as post, patch.object(openai_client, 'checked_json', return_value={}), patch.object(openai_client, '_auth_headers', return_value={}):
            openai_client._post_image_edit('test', 'prompt', 'low', '1024x1024', source)
            self.assertEqual(post.call_args.kwargs['files']['image'], ('photo.jpg', source, 'image/jpeg'))

    def test_a_reference_goes_in_second_as_jpeg(self):
        source = encoded(picture(), 'PNG')
        response = {'data': [{'b64_json': base64.b64encode(source).decode()}], 'usage': {}}
        with patch.object(openai_client, '_post_image_edit', return_value=response) as post:
            openai_client.edit_photo(source, {'recommended_aspect_ratio': 'original'}, single_pass_edit=True,
                                     reference_image_bytes=encoded(picture(), 'PNG'))
        (reference,) = post.call_args.kwargs['extra_jpegs']
        self.assertEqual(Image.open(io.BytesIO(reference)).format, 'JPEG')
        with patch.object(openai_client.requests, 'post') as http, patch.object(openai_client, 'checked_json', return_value={}), patch.object(openai_client, '_auth_headers', return_value={}):
            openai_client._post_image_edit('test', 'prompt', 'low', '1024x1024', b'edited', extra_jpegs=(reference,))
        self.assertEqual(http.call_args.kwargs['files'], [('image[]', ('photo.jpg', b'edited', 'image/jpeg')),
                                                          ('image[]', ('reference1.jpg', reference, 'image/jpeg'))])


if __name__ == '__main__':
    unittest.main()
