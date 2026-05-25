from __future__ import annotations

import logging
import os

import paramiko

logger = logging.getLogger(__name__)


class SSHClient:
    """paramiko SSH client wrapper with bastion support."""

    def __init__(self, timeout: int = 15) -> None:
        self._client = paramiko.SSHClient()
        self._client.load_system_host_keys()
        if os.environ.get("NHW_INSECURE_AUTOADD_HOST_KEY", "").lower() in ("1", "true", "yes"):
            self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            self._client.set_missing_host_key_policy(paramiko.WarningPolicy())
        self._timeout = timeout

    def _load_pkey(self, key_file: str) -> paramiko.PKey | None:
        key_path = os.path.expanduser(key_file)
        if not os.path.exists(key_path):
            return None
        for key_class in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
            try:
                return key_class.from_private_key_file(key_path)
            except paramiko.SSHException:
                continue
        return None

    def connect(
        self,
        hostname: str,
        ip: str,
        port: int = 22,
        username: str = "root",
        key_file: str = "~/.ssh/id_rsa",
        bastion: dict | None = None,
    ) -> None:
        key_path = os.path.expanduser(key_file)
        pkey = self._load_pkey(key_file)

        if pkey is None:
            if os.path.exists(key_path):
                raise RuntimeError(f"无法加载 SSH 私钥 {key_path}，请确认密钥格式为 RSA/Ed25519/ECDSA")
            raise RuntimeError(f"SSH 私钥不存在: {key_path}")

        sock = None
        bastion_client = None
        if bastion:
            bastion_client = paramiko.SSHClient()
            bastion_client.load_system_host_keys()
            if os.environ.get("NHW_INSECURE_AUTOADD_HOST_KEY", "").lower() in ("1", "true", "yes"):
                bastion_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            else:
                bastion_client.set_missing_host_key_policy(paramiko.WarningPolicy())
            bastion_key_path = os.path.expanduser(bastion.get("key_file", "~/.ssh/id_rsa"))
            bastion_pkey = self._load_pkey(bastion.get("key_file", "~/.ssh/id_rsa"))
            if bastion_pkey is None:
                if os.path.exists(bastion_key_path):
                    raise RuntimeError(
                        f"无法加载跳板机 SSH 私钥 {bastion_key_path}，请确认密钥格式为 RSA/Ed25519/ECDSA"
                    )
                raise RuntimeError(f"跳板机 SSH 私钥不存在: {bastion_key_path}")
            bastion_client.connect(
                hostname=bastion.get("ip", bastion["hostname"]),
                port=bastion.get("port", 22),
                username=bastion.get("username", "root"),
                pkey=bastion_pkey,
                timeout=self._timeout,
            )
            transport = bastion_client.get_transport()
            if transport is None:
                raise RuntimeError(f"Bastion transport is None for {bastion['hostname']}")
            dest_addr = (ip, port)
            src_addr = ("", 0)
            sock = transport.open_channel("direct-tcpip", dest_addr, src_addr)

        try:
            self._client.connect(
                hostname=ip,
                port=port,
                username=username,
                pkey=pkey,
                timeout=self._timeout,
                sock=sock,
            )
        except Exception:
            if bastion_client is not None:
                bastion_client.close()
            raise

    def execute(self, command: str) -> str:
        """Run a command and return stdout. Raises RuntimeError on failure."""
        stdin, stdout, stderr = self._client.exec_command(command, timeout=self._timeout)
        exit_status = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        if exit_status != 0:
            logger.debug("Command exited %d: %s", exit_status, command)
        if err:
            logger.debug("stderr for [%s]: %s", command, err.strip())
        return out

    def close(self) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            self._client.close()

    def __enter__(self) -> SSHClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()
