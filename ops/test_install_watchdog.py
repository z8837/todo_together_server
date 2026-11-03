import unittest

from install_watchdog import BEGIN, END, replace_cron_block


class CronInstallationTests(unittest.TestCase):
    def test_preserves_unrelated_jobs_and_environment(self):
        original = "MAILTO=ops\n12 3 * * * /home/tokeii/backup\n"
        updated = replace_cron_block(original, ["* * * * * watchdog"])
        self.assertTrue(updated.startswith(original))
        self.assertIn(BEGIN, updated)

    def test_reinstall_is_idempotent(self):
        first = replace_cron_block("", ["* * * * * watchdog"])
        self.assertEqual(replace_cron_block(first, ["* * * * * watchdog"]), first)

    def test_system_install_can_remove_only_the_managed_jobs(self):
        original = "MAILTO=ops\n12 3 * * * /home/tokeii/backup\n"
        managed = replace_cron_block(original, ["* * * * * watchdog"])
        self.assertEqual(replace_cron_block(managed), original)

    def test_malformed_block_is_never_silently_removed(self):
        for original in (BEGIN + "\n", END + "\n", END + "\n" + BEGIN + "\n"):
            with self.assertRaises(ValueError):
                replace_cron_block(original)


if __name__ == "__main__":
    unittest.main()
