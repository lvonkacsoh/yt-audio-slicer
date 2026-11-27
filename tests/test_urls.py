from yt_audio_slicer.urls import url_key, video_id, video_in_playlist


def test_url_key_equivalent_forms():
    keys = {
        url_key(u)
        for u in [
            "https://youtu.be/bYA3MUO9zKw",
            "https://www.youtube.com/watch?v=bYA3MUO9zKw&t=30",
            "https://music.youtube.com/watch?v=bYA3MUO9zKw",
            "https://youtube.com/shorts/bYA3MUO9zKw",
        ]
    }
    assert len(keys) == 1


def test_video_in_playlist():
    url = "https://www.youtube.com/watch?v=HmZihF5nXEM&list=OLAK5uy_x"
    assert video_in_playlist(url) == ("HmZihF5nXEM", "OLAK5uy_x")
    assert video_in_playlist("https://www.youtube.com/watch?v=HmZihF5nXEM&list=RDHmZihF5nXEM") is None  # a mix
    assert video_in_playlist("https://www.youtube.com/playlist?list=OLAK5uy_x") is None
    assert video_id("https://example.com/x") is None
