import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.rag.handbook import HandbookIndex
from app.rag.store import HandbookStore


def handbook(term):
    return (
        "export const howIvyWorksHandbook = [{id: 'intro', title: 'Ivy', "
        "sections: [{id: 'guide', title: 'Guide', body: ['" + term + "'], "
        "Unpublished_Internal_Notes: ['hiddeninternalword']}]}];"
    ).encode()


class HandbookUploadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'howIvyWorksHandbook.ts'
        self.original = handbook('oldworkflow')
        self.path.write_bytes(self.original)
        self.training_path = Path(self.directory.name) / 'trainingvideo.txt'
        self.training_path.write_text(
            'Video 1 link: https://example.com/training\nScript Video 1:\n'
            'Chapter Guide\n1 chapters\nChapter 1\n\nCalendar setup\n'
            'videoworkflow explains calendar availability.', encoding='utf-8',
        )
        for key, value in {
            'HANDBOOK_PATH': self.path,
            'TRAINING_VIDEO_PATH': self.training_path,
            'STATIC_API_TOKEN': 'test-token',
            'OPENAI_API_TOKEN': '',
            'REDIS_HOST': '',
        }.items():
            patcher = patch.object(settings, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict('os.environ', {'WEB_CONCURRENCY': '1'})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.client.headers['Authorization'] = 'Token test-token'

    def upload(self, content, filename='updated.ts', field='file'):
        return self.client.post('/knowledge-base', files={field: (filename, content, 'text/plain')})

    def test_upload_refreshes_search_chat_and_preserves_sessions(self):
        session_id = self.client.post('/qa-sessions').json()['session_id']
        response = self.upload(handbook('newworkflow'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            'file': self.path.name, 'chapters': 1, 'sections': 1, 'chunks': 3,
        })
        self.assertEqual(self.client.get('/knowledge-base').json(), response.json())
        for term, expected in [('newworkflow', True), ('oldworkflow', False), ('hiddeninternalword', False)]:
            results = self.client.get('/knowledge-base/search', params={'query': term}).json()['results']
            self.assertEqual(bool(results), expected)
        seen = []

        async def reply(client, question, history, chunks):
            seen.extend(chunks)
            yield 'Updated answer'

        with patch('app.routers.qa.stream_reply', reply), patch.object(app.state, 'openai', object()):
            answer = self.client.post(f'/qa-sessions/{session_id}/chat', json={'message': 'newworkflow'})
        self.assertEqual(answer.status_code, 200)
        self.assertIn('"type": "done"', answer.text)
        self.assertTrue(any('newworkflow' in chunk.text for chunk in seen))
        history = self.client.get(f'/qa-sessions/{session_id}').json()['messages']
        self.assertEqual(history[-1]['content'], 'Updated answer')
        self.assertEqual(self.path.read_bytes(), handbook('newworkflow'))
        self.assertTrue(HandbookStore(self.path, 2400).get_index().search('newworkflow'))

    def test_other_worker_refreshes_and_old_snapshot_remains_usable(self):
        worker = HandbookStore(self.path, 2400)
        old_index = worker.get_index()
        self.assertEqual(self.upload(handbook('newworkflow')).status_code, 200)
        self.assertTrue(worker.get_index().search('newworkflow'))
        self.assertFalse(worker.get_index().search('oldworkflow'))
        self.assertTrue(old_index.search('oldworkflow'))

    def test_invalid_uploads_preserve_existing_file_and_index(self):
        for content, filename in [
            (b'', 'empty.ts'), (handbook('new'), 'wrong.txt'),
            (b'\xff', 'bad.ts'), (b'export const somethingElse = [];', 'bad.ts'),
            (b'export const howIvyWorksHandbook = [];', 'bad.ts'),
            (b'export const howIvyWorksHandbook = [null];', 'bad.ts'),
            (b"export const howIvyWorksHandbook = [{id: [], title: 'Bad'}];", 'bad.ts'),
            (b"export const howIvyWorksHandbook = [{id: 'x', title: 'Bad', sections: null}];", 'bad.ts'),
        ]:
            with self.subTest(content=content):
                self.assertEqual(self.upload(content, filename).status_code, 422)
                self.assertEqual(self.path.read_bytes(), self.original)
                self.assertTrue(app.state.handbook.get_index().search('oldworkflow'))

    def test_required_field_auth_and_size_limit(self):
        self.assertEqual(self.client.post('/knowledge-base').status_code, 422)
        self.assertEqual(self.upload(self.original, field='handbook').status_code, 422)
        with patch.object(settings, 'HANDBOOK_MAX_UPLOAD_BYTES', len(self.original) - 1):
            self.assertEqual(self.upload(self.original).status_code, 413)
        with patch.object(settings, 'HANDBOOK_MAX_UPLOAD_BYTES', len(self.original)):
            self.assertEqual(self.upload(self.original).status_code, 200)
        self.client.headers.pop('Authorization')
        self.assertEqual(self.upload(handbook('newworkflow')).status_code, 401)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_write_failure_keeps_old_index_and_cleans_temporary_file(self):
        with patch('app.rag.store.os.replace', side_effect=OSError('read-only filesystem')):
            self.assertEqual(self.upload(handbook('newworkflow')).status_code, 503)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertTrue(app.state.handbook.get_index().search('oldworkflow'))
        self.assertEqual(set(self.path.parent.iterdir()), {self.path, self.training_path})

    def test_training_video_reaches_chat_and_survives_handbook_upload(self):
        session_id = self.client.post('/qa-sessions').json()['session_id']
        for update in (False, True):
            if update:
                self.assertEqual(self.upload(handbook('newworkflow')).status_code, 200)
            results = self.client.get('/knowledge-base/search', params={
                'query': 'videoworkflow',
            }).json()['results']
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['file'], 'trainingvideo.txt')
            self.assertEqual(results[0]['source_id'], 'training-video-1/chapter-1:1')
            self.assertIn('https://example.com/training', results[0]['text'])
            seen = []

            async def reply(client, question, history, chunks):
                seen.extend(chunks)
                yield 'Video answer [training-video-1/chapter-1:1]'

            with patch('app.routers.qa.stream_reply', reply), patch.object(app.state, 'openai', object()):
                answer = self.client.post(f'/qa-sessions/{session_id}/chat', json={
                    'message': 'videoworkflow',
                })
            self.assertEqual(answer.status_code, 200)
            self.assertIn('"type": "done"', answer.text)
            self.assertTrue(any('videoworkflow' in chunk.text for chunk in seen))

    def test_training_file_changes_refresh_workers_without_mutating_old_snapshot(self):
        workers = [app.state.handbook, HandbookStore(self.path, 2400, self.training_path)]
        old_index = workers[1].get_index()
        self.training_path.write_text(
            'Video 2 link: https://example.com/rolematch\nScript Video 2:\n'
            'RoleMatch newvideoworkflow ranks open jobs.', encoding='utf-8',
        )
        for worker in workers:
            index = worker.get_index()
            self.assertFalse(index.search('videoworkflow'))
            result = index.search('newvideoworkflow')[0]
            self.assertEqual(result.source_id, 'training-video-2/overview:1')
            self.assertTrue(index.search('oldworkflow'))
        self.assertTrue(old_index.search('videoworkflow'))

    def test_real_handbook_still_loads(self):
        path = Path(__file__).resolve().parents[1] / 'howIvyWorksHandbook.ts'
        index = HandbookIndex(path)
        self.assertGreater(index.chapter_count, 0)
        self.assertTrue(index.search('interview'))

    def test_real_training_videos_are_all_indexed_with_handbook(self):
        root = Path(__file__).resolve().parents[1]
        index = HandbookStore(root / 'howIvyWorksHandbook.ts', 2400,
                              root / 'trainingvideo.txt').get_index()
        videos = {chunk.chapter_id for chunk in index.chunks if chunk.file == 'trainingvideo.txt'}
        self.assertEqual(videos, {f'training-video-{number}' for number in range(1, 7)})
        self.assertTrue(any(chunk.file == 'trainingvideo.txt' for chunk in index.search('RoleMatch')))
        self.assertTrue(any(chunk.file == 'howIvyWorksHandbook.ts' for chunk in index.search('interview')))
        self.assertEqual(len({chunk.source_id for chunk in index.chunks}), len(index.chunks))


if __name__ == '__main__':
    unittest.main()
