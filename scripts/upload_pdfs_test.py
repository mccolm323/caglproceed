import os
import tempfile
import unittest

from upload_pdfs import upload_all


class FakeS3Client:
    def __init__(self):
        self.calls = []

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.calls.append({"path": path, "bucket": bucket, "key": key, "ExtraArgs": ExtraArgs})


class TestUploadAll(unittest.TestCase):
    def test_uploads_with_pdf_content_type(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "A.pdf")
            with open(path, "wb") as f:
                f.write(b"%PDF-1.4 fake")
            client = FakeS3Client()

            upload_all(client, "my-bucket", d, ["A.pdf"])

            self.assertEqual(len(client.calls), 1)
            call = client.calls[0]
            self.assertEqual(call["path"], path)
            self.assertEqual(call["bucket"], "my-bucket")
            self.assertEqual(call["key"], "A.pdf")
            self.assertEqual(call["ExtraArgs"], {"ContentType": "application/pdf"})

    def test_uploads_multiple_files_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("A.pdf", "B.pdf"):
                with open(os.path.join(d, name), "wb") as f:
                    f.write(b"%PDF-1.4 fake")
            client = FakeS3Client()

            upload_all(client, "my-bucket", d, ["A.pdf", "B.pdf"])

            self.assertEqual([c["key"] for c in client.calls], ["A.pdf", "B.pdf"])


if __name__ == "__main__":
    unittest.main()
