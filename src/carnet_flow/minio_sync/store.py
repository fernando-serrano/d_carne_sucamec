"""Almacenamiento de carnés en MinIO (y un doble en memoria para tests)."""

from __future__ import annotations

import io
import logging
from typing import Protocol

from ..config import Settings


class AlmacenCarnets(Protocol):
    def subir(self, clave: str, data: bytes, meta: dict[str, str]) -> None: ...
    def copiar(self, origen: str, destino: str) -> None: ...
    def eliminar(self, clave: str) -> None: ...
    def listar(self, prefijo: str) -> set[str]: ...


class MinioCarnets:
    def __init__(self, settings: Settings, logger: logging.Logger):
        from minio import Minio

        self.bucket = settings.minio_bucket
        self.log = logger
        self.client = Minio(settings.minio_endpoint, access_key=settings.minio_access_key,
                            secret_key=settings.minio_secret_key, secure=settings.minio_use_ssl)
        # El bucket es de GADSO: no se crea aquí.
        if not self.client.bucket_exists(self.bucket):
            raise RuntimeError(f"MinIO: no existe el bucket '{self.bucket}'")

    def subir(self, clave: str, data: bytes, meta: dict[str, str]) -> None:
        self.client.put_object(self.bucket, clave, io.BytesIO(data), len(data),
                               content_type="application/pdf", metadata=meta)

    def copiar(self, origen: str, destino: str) -> None:
        from minio.commonconfig import CopySource

        self.client.copy_object(self.bucket, destino, CopySource(self.bucket, origen))

    def eliminar(self, clave: str) -> None:
        self.client.remove_object(self.bucket, clave)  # idempotente en S3/MinIO

    def listar(self, prefijo: str) -> set[str]:
        return {o.object_name for o in self.client.list_objects(self.bucket, prefix=prefijo, recursive=True)}


class AlmacenMemoria:
    def __init__(self) -> None:
        self.objetos: dict[str, tuple[bytes, dict[str, str]]] = {}

    def subir(self, clave: str, data: bytes, meta: dict[str, str]) -> None:
        self.objetos[clave] = (data, dict(meta))

    def copiar(self, origen: str, destino: str) -> None:
        data, meta = self.objetos[origen]
        self.objetos[destino] = (data, dict(meta))

    def eliminar(self, clave: str) -> None:
        self.objetos.pop(clave, None)

    def listar(self, prefijo: str) -> set[str]:
        return {k for k in self.objetos if k.startswith(prefijo)}


def crear_almacen(settings: Settings, logger: logging.Logger) -> AlmacenCarnets | None:
    if not settings.minio_activo:
        logger.info("MinIO desactivado (MINIO_ENABLED=0 o sin credenciales): solo Drive")
        return None
    almacen = MinioCarnets(settings, logger)
    logger.info("MinIO: %s/%s (prefijo %s)", settings.minio_endpoint, settings.minio_bucket, settings.minio_prefix)
    return almacen
