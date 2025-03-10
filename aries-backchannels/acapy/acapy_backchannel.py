import asyncio
import functools
import json
import logging
import os
import subprocess
import sys

from timeit import default_timer
from typing import Tuple

from aiohttp import (
    web,
    ClientSession,
    ClientRequest,
    ClientError,
    ClientTimeout,
)

from python.agent_backchannel import (
    AgentBackchannel,
    default_genesis_txns,
    RUN_MODE,
    START_TIMEOUT,
)
from python.utils import flatten, log_msg, output_reader, prompt_loop
from python.storage import (
    get_resource,
    push_resource,
    pop_resource,
    pop_resource_latest,
)

# from helpers.jsonmapper.json_mapper import JsonMapper

LOGGER = logging.getLogger(__name__)

MAX_TIMEOUT = 5

AGENT_NAME = os.getenv("AGENT_NAME", "Agent")

# AIP level is 10 or 20
AIP_CONFIG = int(os.getenv("AIP_CONFIG", "10"))

# backchannel-specific args
EXTRA_ARGS = os.getenv("EXTRA_ARGS")

# other configs ...
DEFAULT_BIN_PATH = "../venv/bin"
DEFAULT_PYTHON_PATH = ".."

if RUN_MODE == "docker":
    DEFAULT_BIN_PATH = "./bin"
    DEFAULT_PYTHON_PATH = "."
elif RUN_MODE == "pwd":
    DEFAULT_BIN_PATH = "./bin"
    DEFAULT_PYTHON_PATH = "."


class AcaPyAgentBackchannel(AgentBackchannel):
    def __init__(
        self,
        ident: str,
        http_port: int,
        admin_port: int,
        genesis_data: str = None,
        params: dict = {},
        extra_args: dict = {},
    ):
        super().__init__(ident, http_port, admin_port, genesis_data, params, extra_args)

        # get aca-py version if available
        self.acapy_version = None
        try:
            with open("./acapy-version.txt", "r") as file:
                self.acapy_version = file.readline()
        except Exception:
            # ignore errors
            pass

        # set the acapy AIP version, defaulting to AIP10
        self.aip_version = "AIP10"

        # set the auto response/request flags
        self.auto_accept_requests = False
        self.auto_respond_messages = False

        # Aca-py : RFC
        self.connectionStateTranslationDict = {
            "invitation": "invited",
            "request": "requested",
            "response": "responded",
            "active": "complete",
        }

        # Aca-py : RFC
        self.issueCredentialStateTranslationDict = {
            "proposal_sent": "proposal-sent",
            "proposal_received": "proposal-received",
            "offer_sent": "offer-sent",
            "offer_received": "offer-received",
            "request_sent": "request-sent",
            "request_received": "request-received",
            "credential_issued": "credential-issued",
            "credential_received": "credential-received",
            "credential_acked": "done",
        }

        # AATH API : Acapy Admin API
        self.issueCredentialv2OperationTranslationDict = {
            "send-proposal": "send-proposal",
            "send-offer": "send-offer",
            "send-request": "send-request",
            "issue": "issue",
            "store": "store",
        }

        # AATH API : Acapy Admin API
        self.proofv2OperationTranslationDict = {
            "create-send-connectionless-request": "create-request",
            "send-presentation": "send-presentation",
            "send-request": "send-request",
            "verify-presentation": "verify-presentation",
            "send-proposal": "send-proposal",
        }

        # AATH API : Acapy Admin API
        self.TopicTranslationDict = {
            "issue-credential": "/issue-credential/",
            "issue-credential-v2": "/issue-credential-2.0/",
            "proof-v2": "/present-proof-2.0/",
        }

        self.credFormatFilterTranslationDict = {"indy": "indy", "json-ld": "ld_proof"}

        self.proofTypeKeyTypeTranslationDict = {
            "Ed25519Signature2018": "ed25519",
            "BbsBlsSignature2020": "bls12381g2",
        }

        # Aca-py : RFC
        self.presentProofStateTranslationDict = {
            "request_sent": "request-sent",
            "request_received": "request-received",
            "proposal_sent": "proposal-sent",
            "proposal_received": "proposal-received",
            "presentation_sent": "presentation-sent",
            "presentation_received": "presentation-received",
            "reject_sent": "reject-sent",
            "verified": "done",
            "presentation_acked": "done",
        }

        # Aca-py : RFC
        self.didExchangeResponderStateTranslationDict = {
            "initial": "invitation-sent",
            "invitation": "invitation-received",
            "request": "request-received",
            "response": "response-sent",
            "?": "abandoned",
            "active": "completed",
            "completed": "completed",
        }

        # Aca-py : RFC
        self.didExchangeRequesterStateTranslationDict = {
            "initial": "invitation-sent",
            "invitation": "invitation-received",
            "request": "request-sent",
            "response": "response-received",
            "?": "abandoned",
            "active": "completed",
        }

    def get_acapy_version_as_float(self):
        # construct some number to compare to with > or < instead of listing out the version number
        # if it starts with zero strip it off
        # if it ends in alpha or RC (or "-<anything>"), change it to .1 or 1
        # strip all dots
        # Does that work if I'm testing 0.5.5.1 hot fix? Just strip off the .1 since there won't be a major change here.

        if not self.acapy_version or 0 == len(self.acapy_version):
            return 0.0

        descriptiveTrailer = "-"
        comparibleVersion = self.acapy_version
        if comparibleVersion.startswith("0"):
            comparibleVersion = comparibleVersion[len("0") :]
        if "." in comparibleVersion:
            stringParts = comparibleVersion.split(".")
            comparibleVersion = "".join(stringParts)
        if descriptiveTrailer in comparibleVersion:
            # This means its not an offical release and came from Master/Main
            # replace with a .1 so that the number is higher than an offical release
            comparibleVersion = comparibleVersion.split(descriptiveTrailer)[0] + ".1"

        #  Make it a number. At this point "0.5.5-RC" should be 55.1. "0.5.4" should be 54.
        return float(comparibleVersion)

    def get_agent_args(self):
        result = [
            ("--endpoint", self.endpoint),
            ("--label", self.label),
            # "--auto-ping-connection",
            # "--auto-accept-invites",
            #"--auto-accept-requests",
            #"--auto-respond-messages",
            ("--inbound-transport", "http", "0.0.0.0", str(self.http_port)),
            ("--outbound-transport", "http"),
            ("--admin", "0.0.0.0", str(self.admin_port)),
            "--admin-insecure-mode",
            "--public-invites",
            ("--wallet-type", self.wallet_type),
            ("--wallet-name", self.wallet_name),
            ("--wallet-key", self.wallet_key),
        ]

        # Backchannel needs to handle operations that may be called in the protocol by the tests
        # but if auto respond or auto accept request in on then the handlers will just return success
        if "--auto-accept-requests" in result:
            self.auto_accept_requests = True
        if "--auto-respond-messages" in result:
            self.auto_respond_messages = True

        if self.get_acapy_version_as_float() > 56:
            result.append(("--auto-provision", "--recreate-wallet"))

        if self.genesis_data:
            result.append(("--genesis-transactions", self.genesis_data))
        if self.seed:
            result.append(("--seed", self.seed))
        if self.storage_type:
            result.append(("--storage-type", self.storage_type))
        if self.postgres:
            result.extend(
                [
                    ("--wallet-storage-type", "postgres_storage"),
                    ("--wallet-storage-config", json.dumps(self.postgres_config)),
                    ("--wallet-storage-creds", json.dumps(self.postgres_creds)),
                ]
            )
        if self.webhook_url:
            result.append(("--webhook-url", self.webhook_url))

        # This code for Tails Server is included here because aca-py does not support the env var directly yet.
        # when it does (and there is talk of supporting YAML) then this code can be removed.
        if os.getenv("TAILS_SERVER_URL") is not None:
            # if the env var is set for tails server then use that.
            result.append(("--tails-server-base-url", os.getenv("TAILS_SERVER_URL")))
        else:
            # if the tails server env is not set use the gov.bc TEST tails server.
            result.append(
                (
                    "--tails-server-base-url",
                    "https://tails-server-test.pathfinder.gov.bc.ca",
                )
            )

        if AIP_CONFIG >= 20 or os.getenv("EMIT_NEW_DIDCOMM_PREFIX") is not None:
            # if the env var is set for tails server then use that.
            result.append(("--emit-new-didcomm-prefix"))

        if AIP_CONFIG >= 20 or os.getenv("EMIT_NEW_DIDCOMM_MIME_TYPE") is not None:
            # if the env var is set for tails server then use that.
            result.append(("--emit-new-didcomm-mime-type"))

        result.append(("--plugin", "http_uniresolver"))
        result.append(("--plugin-config", "/data-mount/plugin-config.yml"))

        # This code for log level is included here because aca-py does not support the env var directly yet.
        # when it does (and there is talk of supporting YAML) then this code can be removed.
        if os.getenv("LOG_LEVEL") is not None:
            result.append(("--log-level", os.getenv("LOG_LEVEL")))

        # result.append(("--trace", "--trace-target", "log", "--trace-tag", "acapy.events", "--trace-label", "acapy",))

        # if self.extra_args:
        #    result.extend(self.extra_args)

        return result

    async def listen_webhooks(self, webhook_port):
        self.webhook_port = webhook_port
        if RUN_MODE == "pwd":
            self.webhook_url = f"http://localhost:{str(webhook_port)}/webhooks"
        else:
            self.webhook_url = (
                f"http://{self.external_host}:{str(webhook_port)}/webhooks"
            )
        app = web.Application()
        app.add_routes([web.post("/webhooks/topic/{topic}/", self._receive_webhook)])
        runner = web.AppRunner(app)
        await runner.setup()
        self.webhook_site = web.TCPSite(runner, "0.0.0.0", webhook_port)
        await self.webhook_site.start()
        print("Listening to web_hooks on port", webhook_port)

    async def _receive_webhook(self, request: ClientRequest):
        topic = request.match_info["topic"]
        payload = await request.json()
        await self.handle_webhook(topic, payload)
        # TODO web hooks don't require a response???
        return web.Response(text="")

    async def handle_webhook(self, topic: str, payload):
        if topic != "webhook":  # would recurse
            handler = f"handle_{topic}"

            # Remove this handler change when bug is fixed.
            if handler == "handle_oob-invitation":
                handler = "handle_oob_invitation"

            method = getattr(self, handler, None)
            # put a log message here
            log_msg("Passing webhook payload to handler " + handler)
            if method:
                await method(payload)
            else:
                log_msg(
                    f"Error: agent {self.ident} "
                    f"has no method {handler} "
                    f"to handle webhook on topic {topic}"
                )
        else:
            log_msg(
                "in webhook, topic is: " + topic + " payload is: " + json.dumps(payload)
            )

    async def handle_connections(self, message):
        if "request_id" in message:
            # This is a did-exchange message based on a Public DID non-invitation
            request_id = message["request_id"]
            push_resource(request_id, "didexchange-msg", message)
        elif message["connection_protocol"] == "didexchange/1.0":
            # This is an did-exchange message based on a Non-Public DID invitation
            invitation_id = message["invitation_msg_id"]
            push_resource(invitation_id, "didexchange-msg", message)
        elif message["connection_protocol"] == "connections/1.0":
            connection_id = message["connection_id"]
            push_resource(connection_id, "connection-msg", message)
        else:
            raise Exception(
                f"Unknown message type in Connections Webhook: {json.dumps(message)}"
            )
        log_msg("Received a Connection Webhook message: " + json.dumps(message))

    async def handle_issue_credential(self, message):
        thread_id = message["thread_id"]
        push_resource(thread_id, "credential-msg", message)
        log_msg("Received Issue Credential Webhook message: " + json.dumps(message))
        if "revocation_id" in message:  # also push as a revocation message
            push_resource(thread_id, "revocation-registry-msg", message)
            log_msg("Issue Credential Webhook message contains revocation info")

    async def handle_issue_credential_v2_0(self, message):
        thread_id = message["thread_id"]
        push_resource(thread_id, "credential-msg", message)
        log_msg("Received Issue Credential v2 Webhook message: " + json.dumps(message))
        if "revocation_id" in message:  # also push as a revocation message
            push_resource(thread_id, "revocation-registry-msg", message)
            log_msg("Issue Credential Webhook message contains revocation info")

    async def handle_present_proof_v2_0(self, message):
        thread_id = message["thread_id"]
        push_resource(thread_id, "presentation-msg", message)
        log_msg("Received a Present Proof v2 Webhook message: " + json.dumps(message))

    async def handle_present_proof(self, message):
        thread_id = message["thread_id"]
        push_resource(thread_id, "presentation-msg", message)
        log_msg("Received a Present Proof Webhook message: " + json.dumps(message))

    async def handle_revocation_registry(self, message):
        # No thread id in the webhook for revocation registry messages
        cred_def_id = message["cred_def_id"]
        push_resource(cred_def_id, "revocation-registry-msg", message)
        log_msg("Received Revocation Registry Webhook message: " + json.dumps(message))

    async def handle_oob_invitation(self, message):
        # No thread id in the webhook for revocation registry messages
        invitation_id = message["invitation_id"]
        push_resource(invitation_id, "oob-inviation-msg", message)
        log_msg(
            "Received Out of Band Invitation Webhook message: " + json.dumps(message)
        )

    async def handle_problem_report(self, message):
        thread_id = message["thread_id"]
        push_resource(thread_id, "problem-report-msg", message)
        log_msg("Received Problem Report Webhook message: " + json.dumps(message))

    async def swap_thread_id_for_exchange_id(self, thread_id, data_type, id_txt):
        timeout = 0
        webcall_returned = None
        while webcall_returned is None or timeout == 20:
            msg = get_resource(thread_id, data_type)
            try:
                ex_id = msg[0][id_txt]
                webcall_returned = True
            except TypeError:
                await asyncio.sleep(1)
                timeout += 1
        if timeout == 20:
            raise TimeoutError(
                "Timeout waiting for web callback to retrieve the thread id based on the exchange id"
            )
        return ex_id

    async def make_admin_request(
        self, method, path, data=None, text=False, params=None
    ) -> (int, str):
        params = {k: v for (k, v) in (params or {}).items() if v is not None}
        async with self.client_session.request(
            method, self.admin_url + path, json=data, params=params
        ) as resp:
            resp_status = resp.status
            resp_text = await resp.text()
            return (resp_status, resp_text)

    async def admin_GET(self, path, text=False, params=None) -> Tuple[int, str]:
        try:
            return await self.make_admin_request("GET", path, None, text, params)
        except ClientError as e:
            self.log(f"Error during GET {path}: {str(e)}")
            raise

    async def admin_DELETE(self, path, text=False, params=None) -> Tuple[int, str]:
        try:
            return await self.make_admin_request("DELETE", path, None, text, params)
        except ClientError as e:
            self.log(f"Error during DELETE {path}: {str(e)}")
            raise

    async def admin_POST(
        self, path, data=None, text=False, params=None
    ) -> Tuple[int, str]:
        try:
            return await self.make_admin_request("POST", path, data, text, params)
        except ClientError as e:
            self.log(f"Error during POST {path}: {str(e)}")
            raise

    async def make_agent_POST_request(
        self, op, rec_id=None, data=None, text=False, params=None
    ) -> Tuple[int, str]:

        if op["topic"] == "connection":
            operation = op["operation"]
            if operation == "create-invitation":
                agent_operation = "/connections/" + operation

                (resp_status, resp_text) = await self.admin_POST(agent_operation)

                # extract invitation from the agent's response
                invitation_resp = json.loads(resp_text)
                resp_text = json.dumps(invitation_resp)

                if resp_status == 200:
                    resp_text = self.agent_state_translation(
                        op["topic"], operation, resp_text
                    )
                return (resp_status, resp_text)

            elif operation == "receive-invitation":
                agent_operation = "/connections/" + operation

                (resp_status, resp_text) = await self.admin_POST(
                    agent_operation, data=data
                )
                if resp_status == 200:
                    resp_text = self.agent_state_translation(
                        op["topic"], None, resp_text
                    )
                return (resp_status, resp_text)

            elif (
                operation == "accept-invitation"
                or operation == "accept-request"
                or operation == "remove"
                or operation == "start-introduction"
                or operation == "send-ping"
            ):
                connection_id = rec_id
                agent_operation = "/connections/" + connection_id + "/" + operation
                log_msg("POST Request: ", agent_operation, data)

                if self.auto_accept_requests and operation == "accept-request":
                    resp_status = 200
                    resp_text = 'Aca-py agent in auto accept request mode. accept-request operation not called.'
                else:
                    # As of adding the Auto Accept and Auto Respond support, it seems a sleep is required here,
                    # or sometimes the agent isn't in the correct state to accept the operation. Not sure why...
                    await asyncio.sleep(1)
                    (resp_status, resp_text) = await self.admin_POST(agent_operation, data)
                    if resp_status == 200: resp_text = self.agent_state_translation(op["topic"], None, resp_text)
                
                log_msg(resp_status, resp_text)
                return (resp_status, resp_text)

        elif op["topic"] == "schema":
            # POST operation is to create a new schema
            agent_operation = "/schemas"
            log_msg(agent_operation, data)

            (resp_status, resp_text) = await self.admin_POST(agent_operation, data)

            log_msg(resp_status, resp_text)
            resp_text = self.move_field_to_top_level(resp_text, "schema_id")
            return (resp_status, resp_text)

        elif op["topic"] == "credential-definition":
            # POST operation is to create a new cred def
            agent_operation = "/credential-definitions"
            log_msg(agent_operation, data)

            (resp_status, resp_text) = await self.admin_POST(agent_operation, data)

            log_msg(resp_status, resp_text)
            resp_text = self.move_field_to_top_level(
                resp_text, "credential_definition_id"
            )
            return (resp_status, resp_text)

        elif op["topic"] == "issue-credential":
            operation = op["operation"]

            acapy_topic = "/issue-credential/"

            if rec_id is None:
                agent_operation = acapy_topic + operation
            else:
                if (
                    operation == "send-offer"
                    or operation == "send-request"
                    or operation == "issue"
                    or operation == "store"
                ):
                    # swap thread id for cred ex id from the webhook
                    cred_ex_id = await self.swap_thread_id_for_exchange_id(
                        rec_id, "credential-msg", "credential_exchange_id"
                    )
                    agent_operation = (
                        acapy_topic + "records/" + cred_ex_id + "/" + operation
                    )
                # Make Special provisions for revoke since it is passing multiple query params not just one id.
                elif operation == "revoke":
                    cred_rev_id = rec_id
                    rev_reg_id = data["rev_registry_id"]
                    publish = data["publish_immediately"]
                    agent_operation = (
                        acapy_topic
                        + operation
                        + "?cred_rev_id="
                        + cred_rev_id
                        + "&rev_reg_id="
                        + rev_reg_id
                        + "&publish="
                        + str(publish).lower()
                    )
                    data = None
                else:
                    agent_operation = acapy_topic + operation

            log_msg(agent_operation, data)

            (resp_status, resp_text) = await self.admin_POST(agent_operation, data)

            log_msg(resp_status, resp_text)
            if resp_status == 200 and self.aip_version != "AIP20":
                resp_text = self.agent_state_translation(op["topic"], None, resp_text)
            return (resp_status, resp_text)

        # Handle issue credential v2 POST operations
        elif op["topic"] == "issue-credential-v2":
            (resp_status, resp_text) = await self.handle_issue_credential_v2_POST(
                op, rec_id=rec_id, data=data
            )
            return (resp_status, resp_text)

        # Handle issue credential v2 POST operations
        elif op["topic"] == "proof-v2":
            (resp_status, resp_text) = await self.handle_proof_v2_POST(
                op, rec_id=rec_id, data=data
            )
            return (resp_status, resp_text)

        elif op["topic"] == "revocation":
            # set the acapyversion to master since work to set it is not complete. Remove when master report proper version
            # self.acapy_version = "0.5.5-RC"
            operation = op["operation"]
            (
                agent_operation,
                admin_data,
            ) = await self.get_agent_operation_acapy_version_based(
                op["topic"], operation, rec_id, data
            )

            log_msg(agent_operation, admin_data)

            if admin_data is None:
                (resp_status, resp_text) = await self.admin_POST(agent_operation)
            else:
                (resp_status, resp_text) = await self.admin_POST(
                    agent_operation, admin_data
                )

            log_msg(resp_status, resp_text)
            if resp_status == 200:
                resp_text = self.agent_state_translation(op["topic"], None, resp_text)
            return (resp_status, resp_text)

        elif op["topic"] == "proof":
            operation = op["operation"]
            if operation == "create-send-connectionless-request":
                operation = "create-request"
            if rec_id is None:
                agent_operation = "/present-proof/" + operation
            else:
                if (
                    operation == "send-presentation"
                    or operation == "send-request"
                    or operation == "verify-presentation"
                    or operation == "remove"
                ):

                    if (
                        operation not in "send-presentation"
                        or operation not in "send-request"
                    ) and (data is None or "~service" not in data):
                        # swap thread id for pres ex id from the webhook
                        pres_ex_id = await self.swap_thread_id_for_exchange_id(
                            rec_id, "presentation-msg", "presentation_exchange_id"
                        )
                    else:
                        # swap the thread id for the pres ex id in the service decorator (this is a connectionless proof)
                        pres_ex_id = data["~service"]["recipientKeys"][0]
                    agent_operation = (
                        "/present-proof/records/" + pres_ex_id + "/" + operation
                    )

                else:
                    agent_operation = "/present-proof/" + operation

            log_msg(agent_operation, data)

            if data is not None:
                # Format the message data that came from the test, to what the Aca-py admin api expects.
                data = self.map_test_json_to_admin_api_json(
                    op["topic"], operation, data
                )

            (resp_status, resp_text) = await self.admin_POST(agent_operation, data)

            log_msg(resp_status, resp_text)
            if resp_status == 200:
                resp_text = self.agent_state_translation(op["topic"], None, resp_text)
            return (resp_status, resp_text)

        # Handle out of band POST operations
        elif op["topic"] == "out-of-band":
            (resp_status, resp_text) = await self.handle_out_of_band_POST(op, data=data)
            return (resp_status, resp_text)

        # Handle did exchange POST operations
        elif op["topic"] == "did-exchange":
            (resp_status, resp_text) = await self.handle_did_exchange_POST(
                op, rec_id=rec_id, data=data
            )
            return (resp_status, resp_text)

        return (501, "501: Not Implemented\n\n".encode("utf8"))

    async def handle_out_of_band_POST(self, op, rec_id=None, data=None):
        operation = op["operation"]
        agent_operation = "/out-of-band/"
        if operation == "send-invitation-message":
            # http://localhost:8022/out-of-band/create-invitation?auto_accept=false&multi_use=false
            # TODO Check the data for auto_accept and multi_use. If it exists use those values then pop them out, otherwise false.
            auto_accept = "false"
            multi_use = "false"
            agent_operation = (
                agent_operation
                + "create-invitation"
                + "?multi_use="
                + multi_use
            )

            # Add handshake protocols to message body
            admindata = {
                "handshake_protocols": [
                    "did:sov:BzCbsNYhMrjHiqZDTUASHg;spec/didexchange/1.0"
                ],
                "use_public_did": data["use_public_did"],
            }
            data = admindata

        elif operation == "receive-invitation":
            # TODO check for Alias and Auto_accept in data to add to the call (works without for now)
            # TODO add use_existing_connection=false in data in the test to pass to here.
            use_existing_connection = "false"
            auto_accept = "false"
            agent_operation = (
                agent_operation
                + "receive-invitation"
                + "?use_existing_connection="
                + use_existing_connection
            )
            # agent_operation = "/didexchange/" + "receive-invitation"

        (resp_status, resp_text) = await self.admin_POST(agent_operation, data)
        if resp_status == 200:
            resp_text = self.agent_state_translation(op["topic"], operation, resp_text)
        return (resp_status, resp_text)

    async def handle_did_exchange_POST(self, op, rec_id=None, data=None):
        operation = op["operation"]
        agent_operation = "/didexchange/"
        if operation == "send-request":
            agent_operation = agent_operation + rec_id + "/accept-invitation"

        elif operation == "receive-invitation":
            agent_operation = agent_operation + operation

        elif operation == "send-response":
            if self.auto_accept_requests:
                resp_status = 200
                resp_text = 'Aca-py agent in auto accept request mode. accept-request operation not called.'
                return (resp_status, resp_text)
            else:
                agent_operation = agent_operation + rec_id + "/accept-request"
                await asyncio.sleep(1)

        elif operation == "create-request-resolvable-did":
            their_public_did = data["their_public_did"]
            agent_operation = (
                agent_operation + "create-request?their_public_did=" + their_public_did
            )
            data = None

        elif operation == "receive-request-resolvable-did":
            # as of PR 1182 in aries-cloudagent-python receive-request is no longer needed.
            # this is done automatically by the responder.
            # The test expects a connection_id returned so, return the last webhook message
            # agent_operation = agent_operation + "receive-request"

            (wh_status, wh_text) = await self.make_agent_GET_request_response(
                op["topic"], rec_id=None, message_name="didexchange-msg"
            )
            return (wh_status, wh_text)

        (resp_status, resp_text) = await self.admin_POST(agent_operation, data)
        if resp_status == 200:
            resp_text = self.agent_state_translation(op["topic"], operation, resp_text)
        return (resp_status, resp_text)

    async def handle_issue_credential_v2_POST(self, op, rec_id=None, data=None):
        operation = op["operation"]
        topic = op["topic"]

        if operation == "prepare-json-ld":
            key_type = self.proofTypeKeyTypeTranslationDict[data["proof_type"]]

            # Retrieve matching dids
            resp_status, resp_text = await self.admin_GET(
                "/wallet/did",
                params={"method": data["did_method"], "key_type": key_type},
            )

            did = None
            if resp_status == 200:
                resp_json = json.loads(resp_text)

                # If there is a matching did use it
                if len(resp_json["results"]) > 0:
                    # Get first matching did
                    did = resp_json["results"][0]["did"]

            # If there was no matching did create a new one
            if not did:
                (resp_status, resp_text) = await self.admin_POST(
                    "/wallet/did/create",
                    {"method": data["did_method"], "options": {"key_type": key_type}},
                )
                if resp_status == 200:
                    resp_json = json.loads(resp_text)
                    did = resp_json["result"]["did"]

            if did:
                resp_text = json.dumps({"did": did})

            log_msg(resp_status, resp_text)
            return (resp_status, resp_text)

        if rec_id is None:
            agent_operation = (
                self.TopicTranslationDict[topic]
                + self.issueCredentialv2OperationTranslationDict[operation]
            )
        else:
            # swap thread id for cred ex id from the webhook
            cred_ex_id = await self.swap_thread_id_for_exchange_id(
                rec_id, "credential-msg", "cred_ex_id"
            )
            agent_operation = (
                self.TopicTranslationDict[topic]
                + "records/"
                + cred_ex_id
                + "/"
                + self.issueCredentialv2OperationTranslationDict[operation]
            )

        # Map AATH filter keys to ACA-Py filter keys
        # e.g. data.filters.json-ld becomes data.filters.ld_proof
        if data and "filter" in data:
            data["filter"] = dict(
                (self.credFormatFilterTranslationDict[name], val)
                for name, val in data["filter"].items()
            )

        log_msg(agent_operation, data)

        (resp_status, resp_text) = await self.admin_POST(agent_operation, data)

        if operation == "store":
            resp_json = json.loads(resp_text)

            if resp_json["ld_proof"]:
                resp_json["json-ld"] = resp_json.pop("ld_proof")

            # Return less ACA-Py specific credential identifier key
            for key in resp_json:
                if resp_json[key] and resp_json[key].get("cred_id_stored"):
                    resp_json[key]["credential_id"] = resp_json[key].get(
                        "cred_id_stored"
                    )

            resp_text = json.dumps(resp_json)

        log_msg(resp_status, resp_text)
        # Looks like all v2 states are RFC states. Yah!
        # if resp_status == 200: resp_text = self.agent_state_translation(topic], None, resp_text)
        resp_text = self.move_field_to_top_level(resp_text, "state")
        return (resp_status, resp_text)

    async def handle_proof_v2_POST(self, op, rec_id=None, data=None):
        operation = op["operation"]
        topic = op["topic"]

        if rec_id is None:
            agent_operation = (
                self.TopicTranslationDict[topic]
                + self.proofv2OperationTranslationDict[operation]
            )
        else:
            # swap thread id for cred ex id from the webhook
            pres_ex_id = await self.swap_thread_id_for_exchange_id(
                rec_id, "presentation-msg", "pres_ex_id"
            )
            agent_operation = (
                self.TopicTranslationDict[topic]
                + "records/"
                + pres_ex_id
                + "/"
                + self.proofv2OperationTranslationDict[operation]
            )

        log_msg(
            f"Data passed to backchannel by test for operation: {agent_operation}", data
        )
        if data is not None:
            # Format the message data that came from the test, to what the Aca-py admin api expects.
            data = self.map_test_json_to_admin_api_json("proof-v2", operation, data)
        log_msg(
            f"Data translated by backchannel to send to agent for operation: {agent_operation}",
            data,
        )
        (resp_status, resp_text) = await self.admin_POST(agent_operation, data)

        log_msg(resp_status, resp_text)
        resp_text = self.move_field_to_top_level(resp_text, "state")
        return (resp_status, resp_text)

    def move_field_to_top_level(self, resp_text, field_to_move):
        # Some reponses have been changed to nest fields that were once at top level.
        # The Test harness expects the these fields to be at the root. Other agents have it at the root.
        # This could be removed if it is common acorss agents to nest these fields in `sent:` for instance.
        resp_json = json.loads(resp_text)
        if field_to_move in resp_json:
            # If it is already a top level field, forget about it.
            return resp_text
        else:
            # Find the field and put a copy as a top level
            for key in resp_json:
                if field_to_move in resp_json[key]:
                    field_value = resp_json[key][field_to_move]
                    resp_json[field_to_move] = field_value
                    return json.dumps(resp_json)

    async def make_agent_GET_request(
        self, op, rec_id=None, text=False, params=None
    ) -> Tuple[int, str]:

        if op["topic"] == "status":
            status = 200 if self.ACTIVE else 418
            status_msg = "Active" if self.ACTIVE else "Inactive"
            return (status, json.dumps({"status": status_msg}))

        if op["topic"] == "version":
            if self.acapy_version is not None:
                status = 200
                # status_msg = json.dumps({"version": self.acapy_version})
                status_msg = self.acapy_version
            else:
                status = 404
                # status_msg = json.dumps({"version": "not found"})
                status_msg = "not found"
            return (status, status_msg)

        elif op["topic"] == "connection":
            if rec_id:
                connection_id = rec_id
                agent_operation = "/connections/" + connection_id
            else:
                agent_operation = "/connections"

            log_msg("GET Request agent operation: ", agent_operation)

            (resp_status, resp_text) = await self.admin_GET(agent_operation)
            if resp_status != 200:
                return (resp_status, resp_text)

            log_msg("GET Request response details: ", resp_status, resp_text)

            resp_json = json.loads(resp_text)
            if rec_id:
                connection_info = {
                    "connection_id": resp_json["connection_id"],
                    "state": resp_json["state"],
                    "connection": resp_json,
                }
                resp_text = json.dumps(connection_info)
            else:
                resp_json = resp_json["results"]
                connection_infos = []
                for connection in resp_json:
                    connection_info = {
                        "connection_id": connection["connection_id"],
                        "state": connection["state"],
                        "connection": connection,
                    }
                    connection_infos.append(connection_info)
                resp_text = json.dumps(connection_infos)
            # translate the state from that the agent gave to what the tests expect
            resp_text = self.agent_state_translation(op["topic"], None, resp_text)
            return (resp_status, resp_text)

        elif op["topic"] == "did":
            agent_operation = "/wallet/did/public"

            (resp_status, resp_text) = await self.admin_GET(agent_operation)
            if resp_status != 200:
                return (resp_status, resp_text)

            resp_json = json.loads(resp_text)
            did = resp_json["result"]

            resp_text = json.dumps(did)
            return (resp_status, resp_text)

        elif op["topic"] == "schema":
            schema_id = rec_id
            agent_operation = "/schemas/" + schema_id

            (resp_status, resp_text) = await self.admin_GET(agent_operation)
            if resp_status != 200:
                return (resp_status, resp_text)

            resp_json = json.loads(resp_text)
            schema = resp_json["schema"]

            resp_text = json.dumps(schema)
            return (resp_status, resp_text)

        elif op["topic"] == "credential-definition":
            cred_def_id = rec_id
            agent_operation = "/credential-definitions/" + cred_def_id

            (resp_status, resp_text) = await self.admin_GET(agent_operation)
            if resp_status != 200:
                return (resp_status, resp_text)

            resp_json = json.loads(resp_text)
            credential_definition = resp_json["credential_definition"]

            resp_text = json.dumps(credential_definition)
            return (resp_status, resp_text)

        elif op["topic"] == "issue-credential":
            # swap thread id for cred ex id from the webhook
            cred_ex_id = await self.swap_thread_id_for_exchange_id(
                rec_id, "credential-msg", "credential_exchange_id"
            )
            agent_operation = (
                self.TopicTranslationDict[op["topic"]] + "records/" + cred_ex_id
            )

            (resp_status, resp_text) = await self.admin_GET(agent_operation)
            if resp_status == 200:
                resp_text = self.agent_state_translation(op["topic"], None, resp_text)
            return (resp_status, resp_text)

        elif op["topic"] == "issue-credential-v2":
            # swap thread id for cred ex id from the webhook
            cred_ex_id = await self.swap_thread_id_for_exchange_id(
                rec_id, "credential-msg", "cred_ex_id"
            )
            agent_operation = (
                self.TopicTranslationDict[op["topic"]] + "records/" + cred_ex_id
            )

            (resp_status, resp_text) = await self.admin_GET(agent_operation)
            resp_text = self.move_field_to_top_level(resp_text, "state")
            return (resp_status, resp_text)

        elif op["topic"] == "credential":
            operation = op["operation"]
            if operation == "revoked":
                agent_operation = "/credential/" + operation + "/" + rec_id
                (resp_status, resp_text) = await self.admin_GET(agent_operation)
                return (resp_status, resp_text)
            else:
                # NOTE: We don't know what type of credential to fetch, so we first try an indy credential.
                # Maybe it would be nice if the test harness passed the credential format that belonged to the
                # credential
                # First try indy credential
                agent_operation = "/credential/" + rec_id
                (resp_status, resp_text) = await self.admin_GET(agent_operation)

                # If not found try w3c credential
                if resp_status == 404:
                    agent_operation = "/credential/w3c/" + rec_id
                    (resp_status, resp_text) = await self.admin_GET(agent_operation)

                    if resp_status == 200:
                        resp_json = json.loads(resp_text)
                        return (
                            resp_status,
                            json.dumps(
                                {
                                    "credential_id": resp_json["record_id"],
                                    "credential": resp_json["cred_value"],
                                }
                            ),
                        )

                return (resp_status, resp_text)

        elif op["topic"] == "proof":
            # swap thread id for pres ex id from the webhook
            pres_ex_id = await self.swap_thread_id_for_exchange_id(
                rec_id, "presentation-msg", "presentation_exchange_id"
            )
            agent_operation = "/present-proof/records/" + pres_ex_id

            (resp_status, resp_text) = await self.admin_GET(agent_operation)
            if resp_status == 200:
                resp_text = self.agent_state_translation(op["topic"], None, resp_text)
            return (resp_status, resp_text)

        elif op["topic"] == "proof-v2":
            # swap thread id for pres ex id from the webhook
            pres_ex_id = await self.swap_thread_id_for_exchange_id(
                rec_id, "presentation-msg", "pres_ex_id"
            )
            agent_operation = (
                self.TopicTranslationDict[op["topic"]] + "records/" + pres_ex_id
            )

            (resp_status, resp_text) = await self.admin_GET(agent_operation)
            # if resp_status == 200: resp_text = self.agent_state_translation(op["topic"], None, resp_text)
            return (resp_status, resp_text)

        elif op["topic"] == "revocation":
            operation = op["operation"]
            (
                agent_operation,
                admin_data,
            ) = await self.get_agent_operation_acapy_version_based(
                op["topic"], operation, rec_id, data=None
            )

            (resp_status, resp_text) = await self.admin_GET(agent_operation)
            return (resp_status, resp_text)

        elif op["topic"] == "did-exchange":

            connection_id = rec_id
            agent_operation = "/connections/" + connection_id

            (resp_status, resp_text) = await self.admin_GET(agent_operation)
            if resp_status == 200:
                resp_text = self.agent_state_translation(op["topic"], None, resp_text)
            return (resp_status, resp_text)

        return (501, "501: Not Implemented\n\n".encode("utf8"))

    async def make_agent_DELETE_request(
        self, op, rec_id=None, data=None, text=False, params=None
    ) -> Tuple[int, str]:
        if op["topic"] == "credential" and rec_id:
            # swap thread id for cred ex id from the webhook
            # cred_ex_id = await self.swap_thread_id_for_exchange_id(rec_id, "credential-msg","credential_exchange_id")
            agent_operation = "/credential/" + rec_id
            # operation = op["operation"]
            # agent_operation, admin_data = await self.get_agent_operation_acapy_version_based(op["topic"], operation, rec_id, data)
            log_msg(agent_operation)

            (resp_status, resp_text) = await self.admin_DELETE(agent_operation)
            if resp_status == 200:
                resp_text = self.agent_state_translation(op["topic"], None, resp_text)
            return (resp_status, resp_text)

        return (501, "501: Not Implemented\n\n".encode("utf8"))

    async def make_agent_GET_request_response(
        self, topic, rec_id=None, text=False, params=None, message_name=None
    ) -> Tuple[int, str]:
        if topic == "connection" and rec_id:
            connection_msg = pop_resource(rec_id, "connection-msg")
            i = 0
            while connection_msg is None and i < MAX_TIMEOUT:
                await asyncio.sleep(1)
                connection_msg = pop_resource(rec_id, "connection-msg")
                i = i + 1

            resp_status = 200
            if connection_msg:
                resp_text = json.dumps(connection_msg)
            else:
                resp_text = "{}"

            return (resp_status, resp_text)

        if topic == "did-exchange" and rec_id:
            didexchange_msg = pop_resource(rec_id, "didexchange-msg")
            i = 0
            while didexchange_msg is None and i < MAX_TIMEOUT:
                await asyncio.sleep(1)
                didexchange_msg = pop_resource(rec_id, "didexchange-msg")
                i = i + 1

            resp_status = 200
            if didexchange_msg:
                resp_text = json.dumps(didexchange_msg)
                resp_text = self.agent_state_translation(topic, None, resp_text)
            else:
                resp_text = "{}"

            return (resp_status, resp_text)

        # Poping webhook messages wihtout an id is unusual. This code may be removed when issue 944 is fixed
        # see https://app.zenhub.com/workspaces/von---verifiable-organization-network-5adf53987ccbaa70597dbec0/issues/hyperledger/aries-cloudagent-python/944
        if topic == "did-exchange" and rec_id is None:
            await asyncio.sleep(1)
            if message_name is not None:
                didexchange_msg = pop_resource_latest(message_name)
            else:
                didexchange_msg = pop_resource_latest("connection-msg")
            i = 0
            while didexchange_msg is None and i < MAX_TIMEOUT:
                await asyncio.sleep(1)
                didexchange_msg = pop_resource_latest("connection-msg")
                i = i + 1

            resp_status = 200
            if didexchange_msg:
                resp_text = json.dumps(didexchange_msg)
                resp_text = self.agent_state_translation(topic, None, resp_text)
            else:
                resp_text = "{}"

            return (resp_status, resp_text)

        elif topic == "issue-credential" and rec_id:
            credential_msg = pop_resource(rec_id, "credential-msg")
            i = 0
            while credential_msg is None and i < MAX_TIMEOUT:
                await asyncio.sleep(1)
                credential_msg = pop_resource(rec_id, "credential-msg")
                i = i + 1

            resp_status = 200
            if credential_msg:
                resp_text = json.dumps(credential_msg)
            else:
                resp_text = "{}"

            return (resp_status, resp_text)

        elif topic == "credential" and rec_id:
            credential_msg = pop_resource(rec_id, "credential-msg")
            i = 0
            while credential_msg is None and i < MAX_TIMEOUT:
                await asyncio.sleep(1)
                credential_msg = pop_resource(rec_id, "credential-msg")
                i = i + 1

            resp_status = 200
            if credential_msg:
                resp_text = json.dumps(credential_msg)
            else:
                resp_text = "{}"

            return (resp_status, resp_text)

        elif topic == "proof" and rec_id:
            presentation_msg = pop_resource(rec_id, "presentation-msg")
            i = 0
            while presentation_msg is None and i < MAX_TIMEOUT:
                await asyncio.sleep(1)
                presentation_msg = pop_resource(rec_id, "presentation-msg")
                i = i + 1

            resp_status = 200
            if presentation_msg:
                resp_text = json.dumps(presentation_msg)
                if resp_status == 200:
                    resp_text = self.agent_state_translation(topic, None, resp_text)
            else:
                resp_text = "{}"

            return (resp_status, resp_text)

        elif topic == "revocation-registry" and rec_id:
            revocation_msg = pop_resource(rec_id, "revocation-registry-msg")
            i = 0
            while revocation_msg is None and i < MAX_TIMEOUT:
                await asyncio.sleep(1)
                revocation_msg = pop_resource(rec_id, "revocation-registry-msg")
                i = i + 1

            resp_status = 200
            if revocation_msg:
                resp_text = json.dumps(revocation_msg)
            else:
                resp_text = "{}"

            return (resp_status, resp_text)

        return (501, "501: Not Implemented\n\n".encode("utf8"))

    def _process(self, args, env, loop):
        proc = subprocess.Popen(
            args,
            env=env,
            encoding="utf-8",
        )
        loop.run_in_executor(
            None,
            output_reader,
            proc.stdout,
            functools.partial(self.handle_output, source="stdout"),
        )
        loop.run_in_executor(
            None,
            output_reader,
            proc.stderr,
            functools.partial(self.handle_output, source="stderr"),
        )
        return proc

    def get_process_args(self, bin_path: str = None):
        # TODO aca-py needs to be in the path so no need to give it a cmd_path
        cmd_path = "aca-py"
        if bin_path is None:
            bin_path = DEFAULT_BIN_PATH
        if bin_path:
            cmd_path = os.path.join(bin_path, cmd_path)
        print("Location of ACA-Py: " + cmd_path)
        if self.get_acapy_version_as_float() > 56:
            return list(flatten(([cmd_path, "start"], self.get_agent_args())))
        else:
            return list(
                flatten((["python3", cmd_path, "start"], self.get_agent_args()))
            )

    async def detect_process(self):
        # text = None

        async def fetch_swagger(url: str, timeout: float):
            text = None
            start = default_timer()
            async with ClientSession(timeout=ClientTimeout(total=3.0)) as session:
                while default_timer() - start < timeout:
                    try:
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                text = await resp.text()
                                break
                    except (ClientError, asyncio.TimeoutError):
                        pass
                    await asyncio.sleep(0.5)
            return text

        status_url = self.admin_url + "/status"
        status_text = await fetch_swagger(status_url, START_TIMEOUT)
        print("Agent running with admin url", self.admin_url)

        if not status_text:
            raise Exception(
                "Timed out waiting for agent process to start. "
                + f"Admin URL: {status_url}"
            )
        ok = False
        try:
            status = json.loads(status_text)
            ok = isinstance(status, dict) and "version" in status
            if ok:
                self.acapy_version = status["version"]
                print(
                    "ACA-py Backchannel running with ACA-py version:",
                    self.acapy_version,
                )
        except json.JSONDecodeError:
            pass
        if not ok:
            raise Exception(
                f"Unexpected response from agent process. Admin URL: {status_url}"
            )

    async def start_process(
        self, python_path: str = None, bin_path: str = None, wait: bool = True
    ):
        my_env = os.environ.copy()
        python_path = DEFAULT_PYTHON_PATH if python_path is None else python_path
        if python_path:
            my_env["PYTHONPATH"] = python_path

        agent_args = self.get_process_args(bin_path)

        # start agent sub-process
        self.log(f"Starting agent sub-process ...")
        self.log(f"agent starting with params: ")
        self.log(agent_args)
        loop = asyncio.get_event_loop()
        self.proc = await loop.run_in_executor(
            None, self._process, agent_args, my_env, loop
        )
        if wait:
            await asyncio.sleep(1.0)
            await self.detect_process()

    def _terminate(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=0.5)
                self.log(f"Exited with return code {self.proc.returncode}")
            except subprocess.TimeoutExpired:
                msg = "Process did not terminate in time"
                self.log(msg)
                raise Exception(msg)

    async def terminate(self):
        loop = asyncio.get_event_loop()
        if self.proc:
            await loop.run_in_executor(None, self._terminate)
        await self.client_session.close()
        if self.webhook_site:
            await self.webhook_site.stop()

    def map_test_json_to_admin_api_json(self, topic, operation, data):
        # If the translation of the json get complicated in the future we might want to consider a switch to JsonMapper or equivalent.
        # json_mapper = JsonMapper()
        # map_specification = {
        #     'name': ['person_name']
        # }
        # JsonMapper(test_json).map(map_specification)

        if topic == "proof":

            if operation == "send-request" or operation == "create-request":
                if operation == "send-proposal":
                    request_type = "presentation_proposal"
                    attachment = "presentations~attach"
                else:
                    request_type = "proof_request"
                    attachment = "request_presentations~attach"

                if (
                    data.get("presentation_proposal", {})
                    .get(attachment, {})
                    .get("data", {})
                    .get("requested_attributes")
                    is None
                ):
                    requested_attributes = {}
                else:
                    requested_attributes = data["presentation_proposal"][attachment][
                        "data"
                    ]["requested_attributes"]

                if (
                    data.get("presentation_proposal", {})
                    .get(attachment, {})
                    .get("data", {})
                    .get("requested_predicates")
                    is None
                ):
                    requested_predicates = {}
                else:
                    requested_predicates = data["presentation_proposal"][attachment][
                        "data"
                    ]["requested_predicates"]

                if (
                    data.get("presentation_proposal", {})
                    .get(attachment, {})
                    .get("data", {})
                    .get("name")
                    is None
                ):
                    proof_request_name = "test proof"
                else:
                    proof_request_name = data["presentation_proposal"][attachment][
                        "data"
                    ]["name"]

                if (
                    data.get("presentation_proposal", {})
                    .get(attachment, {})
                    .get("data", {})
                    .get("version")
                    is None
                ):
                    proof_request_version = "1.0"
                else:
                    proof_request_version = data["presentation_proposal"][attachment][
                        "data"
                    ]["version"]

                if (
                    data.get("presentation_proposal", {})
                    .get(attachment, {})
                    .get("data", {})
                    .get("non_revoked")
                    is None
                ):
                    non_revoked = None
                else:
                    non_revoked = data["presentation_proposal"][attachment]["data"][
                        "non_revoked"
                    ]

                if "connection_id" in data:
                    admin_data = {
                        "comment": data["presentation_proposal"]["comment"],
                        "trace": False,
                        "connection_id": data["connection_id"],
                        request_type: {
                            "name": proof_request_name,
                            "version": proof_request_version,
                            "requested_attributes": requested_attributes,
                            "requested_predicates": requested_predicates,
                        },
                    }
                else:
                    admin_data = {
                        "comment": data["presentation_proposal"]["comment"],
                        "trace": False,
                        request_type: {
                            "name": proof_request_name,
                            "version": proof_request_version,
                            "requested_attributes": requested_attributes,
                            "requested_predicates": requested_predicates,
                        },
                    }
                if non_revoked is not None:
                    admin_data[request_type]["non_revoked"] = non_revoked

            # Make special provisions for proposal. The names are changed in this operation. Should be consistent imo.
            # this whole condition can be removed for V2.0 of the protocol. It will look like more of a send-request in 2.0.
            elif operation == "send-proposal":

                request_type = "presentation_proposal"

                if (
                    data.get("presentation_proposal", {}).get("requested_attributes")
                    == None
                ):
                    requested_attributes = []
                else:
                    requested_attributes = data["presentation_proposal"][
                        "requested_attributes"
                    ]

                if (
                    data.get("presentation_proposal", {}).get("requested_predicates")
                    == None
                ):
                    requested_predicates = []
                else:
                    requested_predicates = data["presentation_proposal"][
                        "requested_predicates"
                    ]

                admin_data = {
                    "comment": data["presentation_proposal"]["comment"],
                    "trace": False,
                    request_type: {
                        "@type": data["presentation_proposal"]["@type"],
                        "attributes": requested_attributes,
                        "predicates": requested_predicates,
                    },
                }

                if "connection_id" in data:
                    admin_data["connection_id"] = data["connection_id"]

            elif operation == "send-presentation":

                if data.get("requested_attributes") == None:
                    requested_attributes = {}
                else:
                    requested_attributes = data["requested_attributes"]

                if data.get("requested_predicates") == None:
                    requested_predicates = {}
                else:
                    requested_predicates = data["requested_predicates"]

                if data.get("self_attested_attributes") == None:
                    self_attested_attributes = {}
                else:
                    self_attested_attributes = data["self_attested_attributes"]

                admin_data = {
                    "comment": data["comment"],
                    "requested_attributes": requested_attributes,
                    "requested_predicates": requested_predicates,
                    "self_attested_attributes": self_attested_attributes,
                }

            else:
                admin_data = data

            # Add on the service decorator if it exists.
            if "~service" in data:
                admin_data["~service"] = data["~service"]

            return admin_data

        if topic == "proof-v2":

            if operation == "send-request":
                request_type = "presentation_request"

                presentation_proposal = data.get("presentation_proposal", {})
                pres_proposal_data = presentation_proposal.get("data", {})
                cred_format = presentation_proposal.get("format")

                if cred_format is None:
                    raise Exception("Credential format not specified for presentation")
                elif cred_format == "indy":
                    requested_attributes = pres_proposal_data.get(
                        "requested_attributes", {}
                    )
                    requested_predicates = pres_proposal_data.get(
                        "requested_predicates", {}
                    )
                    proof_request_name = pres_proposal_data.get("name", "test proof")
                    proof_request_version = pres_proposal_data.get("version", "1.0")
                    non_revoked = pres_proposal_data.get("non_revoked")

                    presentation_request = {
                        cred_format: {
                            "name": proof_request_name,
                            "version": proof_request_version,
                            "requested_attributes": requested_attributes,
                            "requested_predicates": requested_predicates,
                        }
                    }

                    if non_revoked is not None:
                        presentation_request[cred_format]["non_revoked"] = non_revoked

                elif cred_format == "json-ld":
                    # We use DIF format for JSON-LD credentials
                    presentation_request = {"dif": pres_proposal_data}
                else:
                    raise Exception(f"Unknown credential format: {cred_format}")

                admin_data = {
                    "comment": presentation_proposal["comment"],
                    "trace": False,
                    request_type: presentation_request,
                }

                if "connection_id" in presentation_proposal:
                    admin_data["connection_id"] = presentation_proposal["connection_id"]

            elif operation == "send-presentation":

                cred_format = data.get("format")

                if cred_format is None:
                    raise Exception("Credential format not specified for presentation")
                elif cred_format == "indy":
                    requested_attributes = data.get("requested_attributes", {})
                    requested_predicates = data.get("requested_predicates", {})
                    self_attested_attributes = data.get("self_attested_attributes", {})

                    presentation_data = {
                        cred_format: {
                            "requested_attributes": requested_attributes,
                            "requested_predicates": requested_predicates,
                            "self_attested_attributes": self_attested_attributes,
                        }
                    }
                elif cred_format == "json-ld":
                    presentation = data.copy()
                    presentation.pop("format")

                    presentation_data = {"dif": presentation}

                else:
                    raise Exception(f"Unknown credential format: {cred_format}")

                admin_data = {
                    **presentation_data,
                    "comment": data.get("comment", "some comment"),
                }

            else:
                admin_data = data

            # Add on the service decorator if it exists.
            if "~service" in data:
                admin_data["~service"] = data["~service"]

            return admin_data

    def agent_state_translation(self, topic, operation, data):
        # This method is used to translate the agent states passes back in the responses of operations into the states the
        # test harness expects. The test harness expects states to be as they are written in the Protocol's RFC.
        # the following is what the tests/rfc expect vs what aca-py communicates
        # Connection Protocol:
        # Tests/RFC         |   Aca-py
        # invited           |   invitation
        # requested         |   request
        # responded         |   response
        # complete          |   active
        #
        # Issue Credential Protocol:
        # Tests/RFC         |   Aca-py
        # proposal-sent     |   proposal_sent
        # proposal-received |   proposal_received
        # offer-sent        |   offer_sent
        # offer_received    |   offer_received
        # request-sent      |   request_sent
        # request-received  |   request_received
        # credential-issued |   issued
        # credential-received | credential_received
        # done              |   credential_acked
        #
        # Present Proof Protocol:
        # Tests/RFC         |   Aca-py

        resp_json = json.loads(data)
        # Check to see if state is in the json
        if "state" in resp_json:
            agent_state = resp_json["state"]

            # if "did_exchange" in topic:
            #     if "rfc23_state" in resp_json:
            #         rfc_state = resp_json["rfc23_state"]
            #     else:
            #         rfc_state = resp_json["connection"]["rfc23_state"]
            #     data = data.replace('"state"' + ": " + '"' + agent_state + '"', '"state"' + ": " + '"' + rfc_state + '"')
            # else:
            # Check the thier_role property in the data and set the calling method to swap states to the correct role for DID Exchange
            if "their_role" in data:
                # if resp_json["connection"]["their_role"] == "invitee":
                if "invitee" in data:
                    de_state_trans_method = (
                        self.didExchangeResponderStateTranslationDict
                    )
                elif "inviter" in data:
                    de_state_trans_method = (
                        self.didExchangeRequesterStateTranslationDict
                    )
            else:
                # make the trans method any one, since it doesn't matter. It's probably Out of Band.
                de_state_trans_method = self.didExchangeResponderStateTranslationDict

            if topic == "connection":
                # if the response contains didexchange/1.0, swap out the connection states for the did exchange states
                # if "didexchange/1.0" in resp_json["connection_protocol"]:
                if "didexchange/1.0" in data:
                    data = data.replace(
                        '"state"' + ": " + '"' + agent_state + '"',
                        '"state"'
                        + ": "
                        + '"'
                        + de_state_trans_method[agent_state]
                        + '"',
                    )
                else:
                    data = data.replace(
                        agent_state, self.connectionStateTranslationDict[agent_state]
                    )
            elif topic == "issue-credential":
                data = data.replace(
                    agent_state, self.issueCredentialStateTranslationDict[agent_state]
                )
            elif topic == "proof":
                data = data.replace(
                    '"state"' + ": " + '"' + agent_state + '"',
                    '"state"'
                    + ": "
                    + '"'
                    + self.presentProofStateTranslationDict[agent_state]
                    + '"',
                )
            elif topic == "out-of-band":
                data = data.replace(
                    '"state"' + ": " + '"' + agent_state + '"',
                    '"state"' + ": " + '"' + de_state_trans_method[agent_state] + '"',
                )
            elif topic == "did-exchange":
                data = data.replace(
                    '"state"' + ": " + '"' + agent_state + '"',
                    '"state"' + ": " + '"' + de_state_trans_method[agent_state] + '"',
                )
        return data

    async def get_agent_operation_acapy_version_based(
        self, topic, operation, rec_id=None, data=None
    ):
        # Admin api calls may change with acapy releases. For example revocation related calls change
        # between 0.5.4 and 0.5.5. To be able to handle this the backchannel is made aware of the acapy version
        # and constructs the calls based off that version

        # construct some number to compare to with > or < instead of listing out the version number
        comparibleVersion = self.get_acapy_version_as_float()

        if topic == "revocation":
            if operation == "revoke":
                if comparibleVersion > 54:
                    agent_operation = "/revocation/" + operation
                    if "cred_ex_id" in data:
                        admindata = {
                            "cred_ex_ed": data["cred_ex_id"],
                        }
                    else:
                        admindata = {
                            "cred_rev_id": data["cred_rev_id"],
                            "rev_reg_id": data["rev_registry_id"],
                            "publish": str(data["publish_immediately"]).lower(),
                        }
                    data = admindata
                else:
                    agent_operation = "/issue-credential/" + operation

                    if (
                        data is not None
                    ):  # Data should be included with 0.5.4 or lower acapy. Then it takes them as inline parameters.
                        cred_rev_id = data["cred_rev_id"]
                        rev_reg_id = data["rev_registry_id"]
                        publish = data["publish_immediately"]
                        agent_operation = (
                            agent_operation
                            + "?cred_rev_id="
                            + cred_rev_id
                            + "&rev_reg_id="
                            + rev_reg_id
                            + "&publish="
                            + rev_reg_id
                            + str(publish).lower()
                        )
                        data = None
            elif operation == "credential-record":
                agent_operation = "/revocation/" + operation
                if "cred_ex_id" in data:
                    cred_ex_id = data["cred_ex_id"]
                    agent_operation = agent_operation + "?cred_ex_id=" + cred_ex_id
                else:
                    cred_rev_id = data["cred_rev_id"]
                    rev_reg_id = data["rev_registry_id"]
                    agent_operation = (
                        agent_operation
                        + "?cred_rev_id="
                        + cred_rev_id
                        + "&rev_reg_id="
                        + rev_reg_id
                    )
                    data = None
        # elif (topic == "credential"):

        return agent_operation, data


async def main(start_port: int, show_timing: bool = False, interactive: bool = True):

    # check for extra args
    extra_args = {}
    if EXTRA_ARGS:
        print("Got extra args:", EXTRA_ARGS)
        extra_args = json.loads(EXTRA_ARGS)

    genesis = await default_genesis_txns()
    if not genesis:
        print("Error retrieving ledger genesis transactions")
        sys.exit(1)

    agent = None

    try:
        agent = AcaPyAgentBackchannel(
            "aca-py." + AGENT_NAME,
            start_port + 1,
            start_port + 2,
            genesis_data=genesis,
            extra_args=extra_args,
        )

        # start backchannel (common across all types of agents)
        await agent.listen_backchannel(start_port)

        # start aca-py agent sub-process and listen for web hooks
        await agent.listen_webhooks(start_port + 3)
        await agent.register_did()

        await agent.start_process()
        agent.activate()

        # now wait ...
        if interactive:
            async for option in prompt_loop("(X) Exit? [X] "):
                if option is None or option in "xX":
                    break
        else:
            print("Press Ctrl-C to exit ...")
            remaining_tasks = asyncio.Task.all_tasks()
            await asyncio.gather(*remaining_tasks)

    finally:
        terminated = True
        try:
            if agent:
                await agent.terminate()
        except Exception:
            LOGGER.exception("Error terminating agent:")
            terminated = False

    await asyncio.sleep(0.1)

    if not terminated:
        os._exit(1)


def str2bool(v):
    if isinstance(v, bool):
        return v
    if v.lower() in ("yes", "true", "t", "y", "1"):
        return True
    elif v.lower() in ("no", "false", "f", "n", "0"):
        return False
    else:
        raise argparse.ArgumentTypeError("Boolean value expected.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Runs a Faber demo agent.")
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=8020,
        metavar=("<port>"),
        help="Choose the starting port number to listen on",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        type=str2bool,
        default=True,
        metavar=("<interactive>"),
        help="Start agent interactively",
    )
    args = parser.parse_args()

    try:
        asyncio.get_event_loop().run_until_complete(
            main(start_port=args.port, interactive=args.interactive)
        )
    except KeyboardInterrupt:
        os._exit(1)
