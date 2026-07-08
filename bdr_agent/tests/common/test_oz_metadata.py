import unittest

from bdr_agent.common.oz_metadata import runtime_oz_metadata


class OzMetadataTest(unittest.TestCase):
    def test_builds_run_link_from_staging_runtime_env(self) -> None:
        metadata = runtime_oz_metadata(
            {
                "OZ_RUN_ID": "00000000-0000-0000-0000-000000000000",
                "WARP_SERVER_ROOT_URL": "https://staging.example.com",
                "WARP_FOCUS_URL": "warpdev://session/50c0d275c2574b9da0cb89eb8c22c06d",
            }
        )

        self.assertEqual(metadata.oz_run_id, "00000000-0000-0000-0000-000000000000")
        self.assertEqual(
            metadata.oz_run_link,
            "https://oz.staging.example.com/runs/00000000-0000-0000-0000-000000000000",
        )
        self.assertEqual(
            metadata.oz_session_link,
            "warpdev://session/50c0d275c2574b9da0cb89eb8c22c06d",
        )
        self.assertIsNone(metadata.oz_credits_used)

    def test_prefers_explicit_run_link_and_parses_credits_when_present(self) -> None:
        metadata = runtime_oz_metadata(
            {
                "OZ_RUN_ID": "run_123",
                "OZ_RUN_LINK": "https://oz.example.test/runs/run_123",
                "WARP_SERVER_ROOT_URL": "https://staging.example.com",
                "OZ_CREDITS_USED": "1.25",
            }
        )

        self.assertEqual(metadata.oz_run_link, "https://oz.example.test/runs/run_123")
        self.assertEqual(metadata.oz_credits_used, 1.25)

    def test_leaves_metadata_null_when_runtime_env_is_absent(self) -> None:
        metadata = runtime_oz_metadata({})

        self.assertEqual(
            metadata.as_bigquery_fields(),
            {
                "oz_run_id": None,
                "oz_run_link": None,
                "oz_session_link": None,
                "oz_credits_used": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
