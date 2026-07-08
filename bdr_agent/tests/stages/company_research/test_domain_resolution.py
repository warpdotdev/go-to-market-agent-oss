import unittest

from bdr_agent.stages.company_research.hydration import normalize_domain, resolve_company_domain


class DomainResolutionTest(unittest.TestCase):
    def test_normalize_domain_handles_urls_and_www_prefix(self) -> None:
        self.assertEqual(normalize_domain("https://www.Example.com/path?x=1"), "example.com")

    def test_resolve_company_domain_prefers_email_domain(self) -> None:
        resolution = resolve_company_domain(
            email_domain="primary.example.com",
            alternative_email_domain="alternate.example.com",
            website="https://www.website.example.com",
        )

        self.assertEqual(resolution.resolved_company_domain, "primary.example.com")
        self.assertEqual(resolution.resolved_company_domain_source, "company.email_domain")

    def test_resolve_company_domain_does_not_use_contact_email(self) -> None:
        resolution = resolve_company_domain(
            email_domain=None,
            alternative_email_domain=None,
            website=None,
        )

        self.assertIsNone(resolution.resolved_company_domain)
        self.assertIsNone(resolution.resolved_company_domain_source)


if __name__ == "__main__":
    unittest.main()