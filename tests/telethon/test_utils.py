import io
import itertools
import pathlib

import pytest

from telethon import utils
from telethon.tl.types import (
    MessageMediaGame, Game, PhotoEmpty,
    Channel, Community, CommunityForbidden, InputChannel,
    InputPeerChannel, PeerChannel
)


def test_game_input_media_memory_error():
    large_long = 2**62
    media = MessageMediaGame(Game(
        id=large_long,  # <- key to trigger `MemoryError`
        access_hash=large_long,
        short_name='short_name',
        title='title',
        description='description',
        photo=PhotoEmpty(large_long),
    ))
    input_media = utils.get_input_media(media)
    bytes(input_media)  # <- shouldn't raise `MemoryError`


def test_private_get_extension():
    # Positive cases
    png_header = bytes.fromhex('89 50 4e 47 0d 0a 1a 0a  00 00 00 0d 49 48 44 52')
    png_buffer = io.BytesIO(png_header)

    class CustomFd:
        def __init__(self, name):
            self.name = name

    assert utils._get_extension('foo.bar.baz') == '.baz'
    assert utils._get_extension(pathlib.Path('foo.bar.baz')) == '.baz'
    assert utils._get_extension(CustomFd('foo.bar.baz')) == '.baz'

    # Negative cases
    null_header = bytes.fromhex('00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00')
    null_buffer = io.BytesIO(null_header)

    empty_header = bytes()
    empty_buffer = io.BytesIO(empty_header)

    assert utils._get_extension('foo') == ''
    assert utils._get_extension(pathlib.Path('foo')) == ''
    assert utils._get_extension(null_header) == ''
    assert utils._get_extension(null_buffer) == ''
    assert utils._get_extension(null_buffer) == ''  # make sure it did seek back
    assert utils._get_extension(empty_header) == ''
    assert utils._get_extension(empty_buffer) == ''
    assert utils._get_extension(empty_buffer) == ''  # make sure it did seek back
    assert utils._get_extension(CustomFd('foo')) == ''


def _community(access_hash=4242, **kwargs):
    return Community(
        id=777, title='a community', photo=None, date=None,
        access_hash=access_hash, **kwargs
    )


def test_community_entities_in_a_chunk():
    # `_load_next_chunk` maps every entity of a history chunk at once, so an
    # uncastable one takes down the whole chunk rather than just itself.
    chats = [Channel(id=1, title='c', photo=None, date=None, access_hash=1), _community()]

    entities = {utils.get_peer_id(x): x for x in itertools.chain([], chats)}

    assert entities[utils.get_peer_id(PeerChannel(777))] is chats[1]


def test_community_shares_the_channel_id_space():
    # The schema has no peerCommunity: a community is addressed as a channel.
    assert utils.get_peer_id(_community()) == utils.get_peer_id(PeerChannel(777))
    assert utils.get_input_peer(_community()) == InputPeerChannel(777, 4242)
    assert utils.get_input_channel(_community()) == InputChannel(777, 4242)


def test_community_forbidden():
    forbidden = CommunityForbidden(id=99, title='gone', access_hash=7)

    assert utils.get_input_peer(forbidden) == InputPeerChannel(99, 7)
    assert utils.get_peer_id(forbidden) == utils.get_peer_id(PeerChannel(99))


def test_min_community_without_hash_cannot_be_input():
    with pytest.raises(TypeError):
        utils.get_input_peer(_community(access_hash=None, min=True))

    # get_peer_id passes check_hash=False, so it still resolves.
    assert utils.get_peer_id(_community(access_hash=None, min=True)) \
        == utils.get_peer_id(PeerChannel(777))
