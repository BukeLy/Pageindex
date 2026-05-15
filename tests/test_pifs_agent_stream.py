import io
import unittest
from types import SimpleNamespace

from pageindex.filesystem.agent import (
    PIFSAgentStreamObserver,
    normalize_agent_stream_mode,
)


class PIFSAgentStreamTest(unittest.TestCase):
    def raw_event(self, event_type, delta):
        return SimpleNamespace(
            type="raw_response_event",
            data=SimpleNamespace(type=event_type, delta=delta),
        )

    def test_model_stream_prints_output_and_think_deltas(self):
        output = io.StringIO()
        stream_log = []
        observer = PIFSAgentStreamObserver("model", stream_log=stream_log, output=output)

        observer.handle_event(self.raw_event("response.reasoning_summary_text.delta", "look up folder"))
        observer.handle_event(self.raw_event("response.output_text.delta", '{"answer":'))
        observer.handle_event(self.raw_event("response.output_text.delta", '"done"}'))
        observer.finish()

        printed = output.getvalue()
        self.assertIn("[pifs think summary]", printed)
        self.assertIn("look up folder", printed)
        self.assertIn("[pifs output]", printed)
        self.assertIn('{"answer":"done"}', printed.replace("\n", ""))
        self.assertEqual(
            stream_log,
            [
                {"kind": "output", "text": '{"answer":"done"}'},
                {"kind": "think_summary", "text": "look up folder"},
            ],
        )

    def test_tools_mode_does_not_print_model_text(self):
        output = io.StringIO()
        stream_log = []
        observer = PIFSAgentStreamObserver("tools", stream_log=stream_log, output=output)

        observer.handle_event(self.raw_event("response.output_text.delta", "hidden from tools mode"))
        observer.handle_event(self.raw_event("response.function_call_arguments.delta", '{"command":"ls /"}'))
        observer.finish()

        printed = output.getvalue()
        self.assertNotIn("hidden from tools mode", printed)
        self.assertIn("[pifs tool args]", printed)
        self.assertIn('{"command":"ls /"}', printed)
        self.assertEqual(stream_log, [{"kind": "tool_args", "text": '{"command":"ls /"}'}])

    def test_stream_mode_aliases(self):
        self.assertEqual(normalize_agent_stream_mode("think"), "model")
        self.assertEqual(normalize_agent_stream_mode("debug"), "all")
        self.assertEqual(normalize_agent_stream_mode(""), "off")
        with self.assertRaises(ValueError):
            normalize_agent_stream_mode("nope")


if __name__ == "__main__":
    unittest.main()
