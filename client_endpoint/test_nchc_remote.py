from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import nchc_remote


class RuntimeWaitTests(unittest.TestCase):
    def test_ready_runtime_file_connects_without_scheduler_query(self) -> None:
        session = SimpleNamespace(
            runtime_env="/tmp/job.env",
            node="",
            server_port=0,
        )
        ready = {"READY": "1", "NODE": "gpu-node-1", "PORT": "9012"}

        with (
            patch.object(nchc_remote, "_read_runtime_env", return_value=ready),
            patch.object(
                nchc_remote,
                "job_status",
                side_effect=AssertionError("scheduler query should not run"),
            ),
        ):
            url = nchc_remote.wait_and_establish_tunnel(session, 0, timeout_seconds=1)

        self.assertEqual(url, "http://gpu-node-1:9012")
        self.assertEqual(session.node, "gpu-node-1")
        self.assertEqual(session.server_port, 9012)

    def test_live_endpoint_connects_when_ready_flag_lags(self) -> None:
        session = SimpleNamespace(
            runtime_env="/tmp/job.env",
            node="",
            server_port=0,
        )
        starting = {
            "READY": "0",
            "NODE": "gpu-node-2",
            "PORT": "9013",
            "STAGE": "starting-vlm",
        }
        probe = Mock(return_value=True)

        with (
            patch.object(nchc_remote, "_read_runtime_env", return_value=starting),
            patch.object(
                nchc_remote,
                "job_status",
                side_effect=AssertionError("scheduler query should not run"),
            ),
        ):
            url = nchc_remote.wait_and_establish_tunnel(
                session,
                0,
                timeout_seconds=1,
                endpoint_probe=probe,
            )

        self.assertEqual(url, "http://gpu-node-2:9013")
        probe.assert_called_once_with("http://gpu-node-2:9013")
        self.assertEqual(session.node, "gpu-node-2")
        self.assertEqual(session.server_port, 9013)

    def test_squeue_timeout_does_not_trigger_second_sacct_wait(self) -> None:
        ssh = Mock()
        ssh.exec.return_value = (124, "", "")
        session = SimpleNamespace(
            job_id="12345",
            job_submitted_at=0.0,
            ssh=ssh,
        )

        with self.assertRaisesRegex(nchc_remote.RemoteError, "squeue 查詢逾時"):
            nchc_remote.job_status(session, runtime_env={})

        self.assertEqual(ssh.exec.call_count, 1)

    def test_generated_job_requests_three_gpu_stack(self) -> None:
        script = nchc_remote.build_sbatch(
            "/work/user/2026_NCHC_Summer_Intern_Project/server_endpoint",
            "secret",
            "8gpus",
            "",
            "04:00:00",
            32,
            256,
            3,
        )
        self.assertIn("#SBATCH --gres=gpu:3", script)
        self.assertIn('export PATHOVISION_DEFAULT_STUDENT_MODEL=""', script)
        self.assertIn("PROJECT_DIR=/work/user/2026_NCHC_Summer_Intern_Project/server_endpoint", script)
        self.assertIn('STACK_SCRIPT="$PROJECT_DIR/slurm/pathovision_vlm_stack.sbatch"', script)

    def test_invalid_ready_port_is_rejected(self) -> None:
        session = SimpleNamespace(node="", server_port=0)
        with self.assertRaisesRegex(nchc_remote.RemoteError, "Server Port 無效"):
            nchc_remote._endpoint_from_env(
                session,
                {"READY": "1", "NODE": "gpu-node-1", "PORT": "70000"},
            )


if __name__ == "__main__":
    unittest.main()
