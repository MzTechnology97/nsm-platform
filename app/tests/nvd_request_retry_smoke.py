"""NVD requests: a slow or failing answer is tried again with longer timeouts before the advisory sync gives up."""
import httpx

from app import advisory_sources as sources

CPE = "cpe:2.3:o:mikrotik:routeros:*:*:*:*:*:*:*:*"
EMPTY = {"resultsPerPage": 0, "startIndex": 0, "totalResults": 0, "vulnerabilities": []}


def run(answers):
    """Fetch one CPE against a fake NVD that plays ``answers`` in order; returns (pages, timeouts used, pauses, error)."""
    timeouts, pauses = [], []

    def handler(request: httpx.Request):
        timeouts.append(request.extensions["timeout"]["read"])
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer, json=EMPTY) if answer == 200 else httpx.Response(answer, text="unavailable")

    try:
        pages = list(sources.fetch_nvd(sources.NVD_DEFAULT_URL, None, transport=httpx.MockTransport(handler), sleep=pauses.append, cpes=[CPE]))
        return pages, timeouts, pauses, None
    except sources.AdvisorySourceError as exc:
        return None, timeouts, pauses, str(exc)


def main():
    # A timeout, then an answer: the sync goes on, with a longer timeout on the second try.
    pages, timeouts, pauses, error = run([httpx.ReadTimeout("slow"), 200])
    assert error is None and len(pages) == 1 and timeouts == [30.0, 60.0] and pauses == [6.5], (timeouts, pauses, error)

    # 503 twice, then an answer.
    pages, timeouts, _, error = run([503, 503, 200])
    assert error is None and timeouts == [30.0, 60.0, 90.0]

    # Three timeouts: the sync fails, saying so.
    _, timeouts, pauses, error = run([httpx.ReadTimeout("slow")] * 3)
    assert error == "timeout verso NVD (3 tentativi, fino a 90 s)" and timeouts == [30.0, 60.0, 90.0] and pauses == [6.5, 13.0], (error, pauses)

    # Rate limiting and client errors are not retried: they will not get better in seconds.
    _, timeouts, _, error = run([429])
    assert len(timeouts) == 1 and "429" in error
    _, timeouts, _, error = run([404])
    assert len(timeouts) == 1 and "404" in error

    # A persistent 503 ends with the HTTP error after the retries.
    _, timeouts, _, error = run([503, 503, 503])
    assert len(timeouts) == 3 and "503" in error
    print("NVD request retry smoke passed")


if __name__ == "__main__":
    main()
