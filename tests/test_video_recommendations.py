import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock

from app.agents.qa_assistant.main import stream_reply
from app.rag.handbook import Chunk, HandbookIndex


VIDEO_URL = 'https://example.com/calendar-training'


def video_chunk(part=1, url=VIDEO_URL):
    return Chunk(f'training-video-1/chapter-1:{part}', 'trainingvideo.txt',
                 'training-video-1', 'chapter-1', 'Calendar', 'Set availability.', url)


def fake_client(deltas, completed=True):
    async def events():
        for delta in deltas:
            yield SimpleNamespace(type='response.output_text.delta', delta=delta)
        if completed:
            yield SimpleNamespace(type='response.completed')

    @asynccontextmanager
    async def stream(**kwargs):
        yield events()

    return SimpleNamespace(responses=SimpleNamespace(stream=Mock(side_effect=stream)))


class VideoRecommendationTests(unittest.IsolatedAsyncioTestCase):
    async def test_split_citations_append_only_used_video_once(self):
        client = fake_client(['Set availability [training-video-1/',
                              'chapter-1:1] [training-video-1/chapter-1:2].'])
        chunks = [video_chunk(), video_chunk(2),
                  video_chunk(3, 'https://example.com/unused')]
        output = ''.join([part async for part in stream_reply(client, 'Calendar?', [], chunks)])
        self.assertTrue(output.endswith(f'For more information, watch: {VIDEO_URL}'))
        self.assertEqual(output.count(VIDEO_URL), 1)
        self.assertNotIn('https://example.com/unused', output)
        payload = client.responses.stream.call_args.kwargs['input'][-1]['content']
        self.assertIn(f'"video_url": "{VIDEO_URL}"', payload)

    async def test_existing_recommendation_is_not_duplicated(self):
        answer = f'Set availability [training-video-1/chapter-1:1].\nFor more information, watch: {VIDEO_URL}'
        output = ''.join([part async for part in stream_reply(
            fake_client([answer]), 'Calendar?', [], [video_chunk()],
        )])
        self.assertEqual(output, answer)

    async def test_uncited_or_unknown_video_does_not_get_recommended(self):
        for answer in ('Hello!', 'I can help with Octopyd/Ivy product questions.',
                       'Handbook answer [intro/guide:1].',
                       'Unknown source [training-video-99/chapter-1:1].'):
            with self.subTest(answer=answer):
                output = ''.join([part async for part in stream_reply(
                    fake_client([answer]), 'Question?', [], [video_chunk()],
                )])
                self.assertEqual(output, answer)

    async def test_incomplete_stream_does_not_append_recommendation(self):
        parts = []
        with self.assertRaises(RuntimeError):
            async for part in stream_reply(
                fake_client(['Partial [training-video-1/chapter-1:1]'], completed=False),
                'Calendar?', [], [video_chunk()],
            ):
                parts.append(part)
        self.assertNotIn(VIDEO_URL, ''.join(parts))

    def test_long_video_keeps_url_in_every_chunk(self):
        handbook = "export const howIvyWorksHandbook = [{id: 'intro', title: 'Ivy'}];"
        for heading in ('', 'Chapter 1\nCalendar\n'):
            with self.subTest(heading=heading):
                index = HandbookIndex.from_source(
                    handbook, 'handbook.ts', 500,
                    training_source=f'Video 1 link: {VIDEO_URL}\nScript Video 1:\n'
                    + heading + 'Configure your calendar availability. ' * 100,
                )
                chunks = [chunk for chunk in index.chunks if chunk.video_url]
                self.assertGreater(len(chunks), 1)
                for chunk in chunks:
                    self.assertEqual(chunk.video_url, VIDEO_URL)
                    self.assertIn(VIDEO_URL, chunk.text)
                    self.assertEqual(chunk.source()['video_url'], VIDEO_URL)
                self.assertNotIn('video_url', index.chunks[0].source())
