from destinations.alihealth_drugtrace.transport import RequestPacer, _origin_pacer


def test_upload_and_query_instances_share_origin_budget():
    assert _origin_pacer('DMZ.EXAMPLE', 443) is _origin_pacer('dmz.example', 443)
    assert _origin_pacer('dmz.example', 8443) is not _origin_pacer('dmz.example', 443)


def test_every_request_is_paced_and_rate_limit_cooldown_is_not_a_retry():
    now = [100.0]
    waits = []
    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds
    pacer = RequestPacer(clock=lambda: now[0], sleep=sleep)
    pacer.wait()
    pacer.wait()
    assert waits == [1.5]
    pacer.cool_down()
    # Marking a cooldown itself makes no request and performs no sleep.
    assert waits == [1.5]
    pacer.wait()
    assert waits == [1.5, 60.0]
    now[0] += 20
    pacer.wait()
    assert len(waits) == 2


def test_short_sleep_is_rechecked_before_request_slot():
    now = [100.0]
    waits = []
    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds if len(waits) > 1 else seconds / 2
    pacer = RequestPacer(clock=lambda: now[0], sleep=sleep)
    pacer.wait()
    pacer.wait()
    assert waits == [1.5, 0.75]
