import logging
from typing import Optional
from vocode import getenv

from vocode.streaming.models.agent import AgentConfig
from vocode.streaming.models.synthesizer import (
    AzureSynthesizerConfig,
    SynthesizerConfig,
)
from vocode.streaming.models.telephony import CallConfig, TwilioConfig
from vocode.streaming.models.transcriber import (
    DeepgramTranscriberConfig,
    PunctuationEndpointingConfig,
    TranscriberConfig,
)
from vocode.streaming.telephony.config_manager.base_config_manager import (
    BaseConfigManager,
)
from vocode.streaming.telephony.constants import (
    DEFAULT_AUDIO_ENCODING,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_SAMPLING_RATE,
)
from vocode.streaming.telephony.templater import Templater
from vocode.streaming.telephony.utils import create_twilio_client
from vocode.streaming.utils import create_conversation_id


class OutboundCall:
    def __init__(
        self,
        base_url: str,
        to_phone: str,
        from_phone: str,
        config_manager: BaseConfigManager,
        agent_config: AgentConfig,
        twilio_config: Optional[TwilioConfig] = None,
        transcriber_config: Optional[TranscriberConfig] = None,
        synthesizer_config: Optional[SynthesizerConfig] = None,
        conversation_id: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
        mobile_only: bool = True,
        digits: Optional[
            str
        ] = None,  # Keys to press when the call connects, see send_digits https://www.twilio.com/docs/voice/api/call-resource#create-a-call-resource
    ):
        self.base_url = base_url
        self.to_phone = to_phone
        self.digits = digits
        self.from_phone = from_phone
        self.mobile_only = mobile_only
        self.config_manager = config_manager
        self.agent_config = agent_config
        self.transcriber_config = transcriber_config or DeepgramTranscriberConfig(
            sampling_rate=DEFAULT_SAMPLING_RATE,
            audio_encoding=DEFAULT_AUDIO_ENCODING,
            chunk_size=DEFAULT_CHUNK_SIZE,
            model="phonecall",
            tier="nova",
            endpointing_config=PunctuationEndpointingConfig(),
        )
        self.synthesizer_config = synthesizer_config or AzureSynthesizerConfig(
            sampling_rate=DEFAULT_SAMPLING_RATE, audio_encoding=DEFAULT_AUDIO_ENCODING
        )
        self.conversation_id = conversation_id or create_conversation_id()
        self.logger = logger or logging.getLogger(__name__)
        self.twilio_config = twilio_config or TwilioConfig(
            account_sid=getenv("TWILIO_ACCOUNT_SID"),
            auth_token=getenv("TWILIO_AUTH_TOKEN"),
        )
        self.twilio_client = create_twilio_client(self.twilio_config)
        self.twilio_sid = None
        self.templater = Templater()

    def create_twilio_call(
        self, to_phone: str, from_phone: str, digits: str = "", record: bool = False
    ) -> str:
        twiml = self.templater.get_connection_twiml(
            base_url=self.base_url, call_id=self.conversation_id
        )
        twilio_call = self.twilio_client.calls.create(
            twiml=twiml.body.decode("utf-8"),
            to=to_phone,
            from_=from_phone,
            send_digits=digits,
            record=record,
        )
        return twilio_call.sid

    def validate_outbound_call(
        self,
        to_phone: str,
        from_phone: str,
        mobile_only: bool = True,
    ):
        if len(to_phone) < 8:
            raise ValueError("Invalid 'to' phone")

        if not mobile_only:
            return
        line_type_intelligence = (
            self.twilio_client.lookups.v2.phone_numbers(to_phone)
            .fetch(fields="line_type_intelligence")
            .line_type_intelligence
        )
        if not line_type_intelligence or (
            line_type_intelligence and line_type_intelligence["type"] != "mobile"
        ):
            raise ValueError("Can only call mobile phones")

    def start(self):
        self.logger.debug("Starting outbound call")
        self.validate_outbound_call(
            to_phone=self.to_phone,
            from_phone=self.from_phone,
            mobile_only=self.mobile_only,
        )
        self.twilio_sid = self.create_twilio_call(
            to_phone=self.to_phone,
            from_phone=self.from_phone,
            record=self.twilio_config.record,
            digits=self.digits,
        )
        call_config = CallConfig(
            transcriber_config=self.transcriber_config,
            agent_config=self.agent_config,
            synthesizer_config=self.synthesizer_config,
            twilio_config=self.twilio_config,
            twilio_sid=self.twilio_sid,
            twilio_from=self.from_phone,
            twilio_to=self.to_phone,
        )
        self.config_manager.save_config(self.conversation_id, call_config)

    def end(self):
        response = self.twilio_client.calls(self.twilio_sid).update(status="completed")
        return response.status == "completed"
