"""Offline tests for the S3 fetcher (a fake boto3 client; no AWS, no network)."""

import sys

import pytest

from dawem_ai.exceptions import SourceObjectNotFoundError, SourceStorageError
from dawem_ai.ingestion import Boto3S3Fetcher


class FakeClient:
    def __init__(self, *, error=None, size=123, body=b"hello"):
        self.error, self.size, self.body = error, size, body
        self.calls = []

    def head_object(self, Bucket, Key):
        self.calls.append(("head", Bucket, Key))
        if self.error:
            raise self.error
        return {"ContentLength": self.size}

    def download_file(self, bucket, key, filename):
        self.calls.append(("download", bucket, key))
        if self.error:
            raise self.error
        with open(filename, "wb") as fh:
            fh.write(self.body)


def test_size_and_download_use_the_given_bucket_and_key(tmp_path):
    client = FakeClient(size=5)
    fetcher = Boto3S3Fetcher(client)
    assert fetcher.size(bucket="b-1", key="uploads/a b.pdf") == 5
    dest = tmp_path / "download.pdf"
    fetcher.download(bucket="b-1", key="uploads/a b.pdf", dest=dest)
    assert dest.read_bytes() == b"hello"
    assert client.calls == [("head", "b-1", "uploads/a b.pdf"), ("download", "b-1", "uploads/a b.pdf")]


def test_boto3_is_imported_lazily_so_the_service_boots_without_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", None)  # makes `import boto3` raise ImportError
    fetcher = Boto3S3Fetcher()  # constructing must not import boto3
    with pytest.raises(ImportError):
        fetcher.client


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
def test_missing_object_maps_to_source_object_not_found(code, tmp_path):
    botocore = pytest.importorskip("botocore.exceptions")
    err = botocore.ClientError({"Error": {"Code": code, "Message": "x"}}, "HeadObject")
    fetcher = Boto3S3Fetcher(FakeClient(error=err))
    with pytest.raises(SourceObjectNotFoundError):
        fetcher.size(bucket="b-1", key="k.pdf")
    with pytest.raises(SourceObjectNotFoundError):
        fetcher.download(bucket="b-1", key="k.pdf", dest=tmp_path / "x")


@pytest.mark.parametrize("code", ["AccessDenied", "SlowDown", "InternalError"])
def test_other_aws_errors_map_to_source_storage_error_without_details(code):
    botocore = pytest.importorskip("botocore.exceptions")
    err = botocore.ClientError({"Error": {"Code": code, "Message": "arn:aws:s3:::secret-bucket"}}, "HeadObject")
    with pytest.raises(SourceStorageError) as info:
        Boto3S3Fetcher(FakeClient(error=err)).size(bucket="b-1", key="k.pdf")
    assert "secret-bucket" not in str(info.value)


def test_connection_errors_map_to_source_storage_error_and_bugs_pass_through():
    botocore = pytest.importorskip("botocore.exceptions")
    conn = botocore.EndpointConnectionError(endpoint_url="https://s3.example")
    with pytest.raises(SourceStorageError):
        Boto3S3Fetcher(FakeClient(error=conn)).size(bucket="b-1", key="k.pdf")
    with pytest.raises(RuntimeError):  # not an AWS error: a bug, do not disguise it
        Boto3S3Fetcher(FakeClient(error=RuntimeError("bug"))).size(bucket="b-1", key="k.pdf")
