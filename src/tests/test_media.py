"""Tests for picker.media — file type classification."""
from picker.media import is_video, VIDEO_EXTENSIONS, MEDIA_EXTENSIONS
from picker.image_manager import SUPPORTED_EXTENSIONS, FILE_TYPE_MAP, active_extensions


class TestIsVideo:
    def test_mp4(self):
        assert is_video("test.mp4") is True

    def test_mov(self):
        assert is_video("clip.MOV") is True

    def test_jpg_not_video(self):
        assert is_video("photo.jpg") is False

    def test_case_insensitive(self):
        assert is_video("VIDEO.MKV") is True


class TestExtensions:
    def test_video_extensions_disjoint_from_image(self):
        assert VIDEO_EXTENSIONS.isdisjoint(SUPPORTED_EXTENSIONS)

    def test_media_is_union(self):
        assert MEDIA_EXTENSIONS == SUPPORTED_EXTENSIONS | VIDEO_EXTENSIONS

    def test_file_type_map_covers_supported(self):
        mapped = set()
        for exts in FILE_TYPE_MAP.values():
            mapped |= exts
        assert mapped == SUPPORTED_EXTENSIONS


class TestActiveExtensions:
    def test_none_returns_all(self):
        exts = active_extensions(None, include_videos=False)
        assert ".jpg" in exts
        assert ".cr2" in exts

    def test_jpeg_only(self):
        exts = active_extensions(["jpeg"], include_videos=False)
        assert exts == {".jpg", ".jpeg"}

    def test_include_videos(self):
        exts = active_extensions(["jpeg"], include_videos=True)
        assert ".mp4" in exts
        assert ".jpg" in exts
