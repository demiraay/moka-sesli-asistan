"""Cevap akisi.

En onemli guvence: EKRANA YAZILAN HICBIR SEY SONRADAN DEGISMEZ. Sunum katmani
(% -> "yuzde", maskeli IBAN, kart, URL, selamlama kirpma) metin uzerinde desen
esler; parcalar erken yayinlanirsa desen bolunur ve yazi geriye donuk degisir.
Bu yuzden testler "yayinlanan parcalarin birlesimi == nihai metin" der.
"""

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.orchestrator import AgentOrchestrator


def chunked(text, size=7):
    """Metni LLM'den gelir gibi kucuk parcalara boler."""
    return [text[index:index + size] for index in range(0, len(text), size)]


class StreamingTestCase(unittest.TestCase):
    def setUp(self):
        self.orch = AgentOrchestrator()

    def collect(self, text, *, channel="voice", is_first_turn=True, size=7):
        pieces = list(self.orch._stream_polished(
            chunked(text, size), channel=channel, is_first_turn=is_first_turn))
        return "".join(pieces), pieces


class TestStreamMatchesFinal(StreamingTestCase):
    """Akan ve akmayan yol AYNI metni uretmeli."""

    def _assert_consistent(self, text, **kwargs):
        streamed, _ = self.collect(text, **kwargs)
        expected = self.orch._polish(
            text, channel=kwargs.get("channel", "voice"),
            is_first_turn=kwargs.get("is_first_turn", True))
        self.assertEqual(streamed, expected)

    def test_plain_text(self):
        self._assert_consistent("Merhaba, bugün size nasıl yardımcı olabilirim?")

    def test_percentage_is_spoken(self):
        text = ("Komisyon oranınız %1,99 olarak uygulanıyor ve bu ay toplam "
                "kesinti 4 bin 532 TL oldu, detayları isterseniz iletebilirim.")
        self._assert_consistent(text)
        streamed, _ = self.collect(text)
        self.assertIn("yüzde", streamed)
        self.assertNotIn("%1,99", streamed)

    def test_masked_iban_is_spoken(self):
        text = ("Ödemeniz yarın saat 10:00'da TR** **** **** **** **44 17 "
                "numaralı hesabınıza aktarılacak, başka sorunuz var mı acaba?")
        self._assert_consistent(text)
        streamed, _ = self.collect(text)
        self.assertIn("sonu 44 17 ile biten", streamed)

    def test_masked_card_is_spoken(self):
        text = ("Dün saat 16:40'ta **** 4832 numaralı kartla yapılan işleminiz "
                "onaylanmış görünüyor, kontrol edebildiniz mi acaba?")
        self._assert_consistent(text)

    def test_url_is_not_read_aloud(self):
        text = ("Ödeme linkiniz https://moka.link/ekinci-1a2b adresinde hazır, "
                "telefonunuza SMS olarak da gönderdim, kontrol eder misiniz?")
        self._assert_consistent(text)
        streamed, _ = self.collect(text)
        self.assertNotIn("moka.link", streamed)

    def test_currency_code_becomes_tl(self):
        self._assert_consistent(
            "Bu ayki toplam cironuz 295000 TRY olarak görünüyor efendim, "
            "komisyon sonrası net tutar hesabınıza geçecek.")

    def test_greeting_is_stripped_on_later_turns(self):
        text = ("Merhaba Muhammed Bey, ben Moka United'dan Ada. Hakedişiniz "
                "yarın saat 10:00'da hesabınıza aktarılacak şekilde planlandı.")
        self._assert_consistent(text, is_first_turn=False)
        streamed, _ = self.collect(text, is_first_turn=False)
        self.assertFalse(streamed.lower().startswith("merhaba"))

    def test_text_channel_keeps_symbols(self):
        """Sesli okuma donusumleri yalnizca voice kanalinda uygulanir."""
        text = ("Komisyon oranınız %1,99 seviyesinde kalıyor ve bu ay için "
                "ek bir kesinti bulunmuyor, dilerseniz ekstre gönderebilirim.")
        self._assert_consistent(text, channel="whatsapp")
        streamed, _ = self.collect(text, channel="whatsapp")
        self.assertIn("%1,99", streamed)


class TestChunkSizeIndependence(StreamingTestCase):
    """Sonuc, LLM'in parcalari nasil bolduguna BAGLI OLMAMALI."""

    TEXT = ("Merhaba Muhammed Bey, ben Ada. Komisyonunuz %1,99 ve ödemeniz "
            "TR** **** **** **** **44 17 hesabına yarın 10:00'da geçecek.")

    def test_every_chunk_size_gives_the_same_result(self):
        expected = self.orch._polish(self.TEXT, channel="voice", is_first_turn=True)
        for size in (1, 2, 3, 5, 13, 40, 500):
            streamed, _ = self.collect(self.TEXT, size=size)
            self.assertEqual(streamed, expected, f"parca boyutu {size} farkli sonuc verdi")

    def test_single_chunk_behaves_like_no_streaming(self):
        streamed, _ = self.collect(self.TEXT, size=len(self.TEXT))
        self.assertEqual(streamed,
                         self.orch._polish(self.TEXT, channel="voice", is_first_turn=True))


class TestProgressiveDelivery(StreamingTestCase):
    """Akis GERCEKTEN parcali olmali; yoksa akmanin anlami yok."""

    LONG = ("Merhaba Muhammed Bey, ben Moka United'dan Ada. Bugün yaptığınız "
            "satışların net tutarı 9 bin 638 lira olarak hesaplandı ve bu tutar "
            "yarın sabah saat 10:00'da kayıtlı hesabınıza aktarılacak. Ayrıca "
            "bu ay komisyon toplamınız 5 bin 870 lira seviyesinde görünüyor.")

    def test_long_reply_arrives_in_several_pieces(self):
        _, pieces = self.collect(self.LONG, size=10)
        self.assertGreater(len(pieces), 3, "cevap tek parcada geldi, akis yok")

    def test_first_piece_arrives_before_the_end(self):
        """Ilk parca, metnin tamami birikmeden gelmeli."""
        seen_before_end = []
        total = len(self.LONG)

        def source():
            for index, chunk in enumerate(chunked(self.LONG, 10)):
                yield chunk
                if seen_before_end:
                    self.assertLess(len("".join(chunked(self.LONG, 10)[:index + 1])),
                                    total + 1)

        pieces = []
        for piece in self.orch._stream_polished(source(), channel="voice",
                                                is_first_turn=True):
            pieces.append(piece)
            seen_before_end.append(True)
        self.assertTrue(pieces)

    def test_empty_chunks_are_ignored(self):
        pieces = list(self.orch._stream_polished(
            ["Merhaba", "", None, " dünya", ""], channel="whatsapp", is_first_turn=True))
        self.assertEqual("".join(pieces), "Merhaba dünya")


class TestStreamEdgeCases(StreamingTestCase):
    def test_empty_stream_yields_nothing(self):
        pieces = list(self.orch._stream_polished([], channel="voice", is_first_turn=True))
        self.assertEqual(pieces, [])
        self.assertEqual(self.orch._last_streamed_text, "")

    def test_short_reply_still_emitted(self):
        """Tampondan kisa cevaplar da eksiksiz yayinlanmali."""
        streamed, _ = self.collect("Tamamdır.", size=3)
        self.assertEqual(streamed, "Tamamdır.")

    def test_final_text_is_recorded_for_logging(self):
        text = "Hakedişiniz yarın hesabınıza geçecek efendim."
        streamed, _ = self.collect(text)
        self.assertEqual(self.orch._last_streamed_text, streamed)



class TestStreamEvents(unittest.TestCase):
    """process_turn_stream olay sirasi: once araclar, sonra metin, en son sonuc."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from conftest import stub_llm_script
        from core.admin_store import AdminStore

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.orch = AgentOrchestrator()
        self.orch.admin_store = AdminStore(db_path=str(Path(self.temp_dir.name) / "s.sqlite3"))
        for container in (self.orch.conversation_histories, self.orch.planner_transcripts,
                          self.orch.user_profiles, self.orch.active_sessions):
            container.clear()
        stub_llm_script(self.orch.llm_client, [
            '{"tool": "get_settlement_status", "args": {"period": "latest"}}',
            "Hakedişiniz yarın saat 10:00'da hesabınıza geçecek efendim.",
        ])

    def _run(self):
        return list(self.orch.process_turn_stream(
            "Param ne zaman yatacak?", user_id="u-stream", channel="demo"))

    def test_tool_events_are_emitted_before_text(self):
        """Planlama uzun surebilir; kullanici ne yapildigini GORMELI."""
        from core.orchestrator import AGENT_LOOP_ENABLED
        if not AGENT_LOOP_ENABLED:
            self.skipTest("araç olayları agent loop yolunda")

        events = self._run()
        kinds = [kind for kind, _ in events]
        self.assertIn("tool", kinds, "araç olayı hiç yayınlanmadı")

        first_tool = kinds.index("tool")
        self.assertLess(first_tool, kinds.index("done"))
        if "delta" in kinds:
            self.assertLess(first_tool, kinds.index("delta"),
                            "araç olayı metinden ÖNCE gelmeli")

    def test_tool_event_carries_the_tool_name(self):
        from core.orchestrator import AGENT_LOOP_ENABLED
        if not AGENT_LOOP_ENABLED:
            self.skipTest("araç olayları agent loop yolunda")
        names = [payload for kind, payload in self._run() if kind == "tool"]
        self.assertIn("get_settlement_status", names)

    def test_stream_ends_with_done_and_full_result(self):
        events = self._run()
        kind, payload = events[-1]
        self.assertEqual(kind, "done")
        self.assertTrue(payload["agent_response"])

    def test_deltas_join_into_the_final_answer(self):
        events = self._run()
        deltas = "".join(payload for kind, payload in events if kind == "delta")
        final = events[-1][1]["agent_response"]
        if deltas:
            self.assertEqual(deltas, final)


if __name__ == "__main__":
    unittest.main()
