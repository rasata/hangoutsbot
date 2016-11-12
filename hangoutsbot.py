#!/usr/bin/env python3

import logging
import asyncio
import re
import os
import sys
import hangups

import settings

from models.user import User
from models.conversation import Conversation
from models.message import Message
from models.command import Command

from utils.commands import register_commands
from utils.enums import EventType, ConversationType
from utils.textutils import spacing

from datetime import datetime

logger = logging.getLogger(__name__)
logging.basicConfig(format="%(name)s - %(levelname)s: %(asctime)s | %(message)s")
logger.setLevel(logging.DEBUG)


class HangoutsBot(object):

    def __init__(self):
        self.command_matcher = re.compile(settings.COMMAND_MATCH_REGEX)
        register_commands()
        self.client = hangups.client.Client(self.login())
        self.user = User.get_or_create(id=settings.BOT_ID, defaults={'first_name': 'Bot', 'last_name': 'User'})[0]

    def login(self):
        return hangups.auth.get_auth_stdin(settings.COOKIES_FILE_PATH)

    def run(self):
        logger.debug(self.client)
        self.client.on_state_update.add_observer(self.handle_update)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.client.connect())

    @asyncio.coroutine
    def handle_update(self, state_update):
        logger.debug("Handling event update")
        if state_update.event_notification.event.event_type == EventType.EVENT_TYPE_REGULAR_CHAT_MESSAGE.value:
            yield from self.handle_message(state_update)
        else:
            pass

    @asyncio.coroutine
    def handle_message(self, state_update):
        logger.debug("Handling message")
        conversation = self.get_or_create_conversation(state_update.conversation)
        if state_update.event_notification.event.sender_id.gaia_id == settings.BOT_ID:
            message = Message.create(conversation=conversation, user=self.user, text="".join(
                [seg.text for seg in state_update.event_notification.event.chat_message.message_content.segment]), time=datetime.now())
            message.conversation.logger.info(message.text.replace("\n", " "), extra={
                "username": message.user.username,
                "message_time": datetime.strftime(message.time, "%Y-%m-%d %X"),
            })
            return True
        self.check_conversation_participants(state_update.conversation)
        try:
            sending_user = User.get(User.id == state_update.event_notification.event.sender_id.gaia_id)
        except User.DoesNotExist:
            user_id = state_update.event_notification.event.sender_id.gaia_id
            sending_user = self.create_user_from_id(user_id, state_update.conversation)
        message_body = ""
        for seg in state_update.event_notification.event.chat_message.message_content.segment:
            message_body += seg.text
        message = Message.create(conversation=conversation, user=sending_user, text=message_body, time=datetime.now())
        message.conversation.logger.info(message.text.replace("\n", " "), extra={
            "username": message.user.username,
            "message_time": datetime.strftime(message.time, "%Y-%m-%d %X"),
        })

        matched = self.command_matcher.match(message.text)
        if matched:
            try:
                cmd_to_run = Command.get(name=matched.group(1))
                yield from cmd_to_run.run(bot=self, conversation=message.conversation, user=message.user, args=message.text.split()[1:])
            except Command.DoesNotExist:
                pass
        return True

    def create_user_from_id(self, user_id, conversation):
        logger.debug("Creating User with id {}".format(user_id))
        participant_object = None
        for participant in conversation.participant_data:
            if participant.id.gaia_id == user_id:
                participant_object = participant
                break
        name_split = participant_object.fallback_name.split(" ", 1)
        if len(name_split) > 1:
            user = User.create(id=user_id, first_name=name_split[0], last_name=name_split[1])
        else:
            user = User.create(id=user_id, first_name=name_split[0], last_name="")
        return user

    def get_or_create_conversation(self, conversation):
        try:
            conv = Conversation.get(Conversation.id == conversation.conversation_id.id)
        except Conversation.DoesNotExist:
            logger.debug("Creating Conversation with id {}".format(conversation.conversation_id.id))
            is_group = conversation.type == ConversationType.CONVERSATION_TYPE_GROUP.value
            conv = Conversation.create(id=conversation.conversation_id.id, group=is_group)
        return conv

    def check_conversation_participants(self, conversation):
        logger.debug("Checking Conversation participants")
        conv = self.get_or_create_conversation(conversation)
        for participant in conversation.participant_data:
            try:
                user = User.get(User.id == participant.id.gaia_id)
            except User.DoesNotExist:
                user = self.create_user_from_id(participant.id.gaia_id, conversation)
            if user not in conv.members:
                logger.debug("Adding User {} to Conversation {}".format(user.id, conv.id))
                conv.members.add(user)
        return True

    @asyncio.coroutine
    def send_message(self, conversation, message, filter_to_use):
        if filter_to_use == "spacing":
            message = spacing(message)
        request = hangups.hangouts_pb2.SendChatMessageRequest(
            request_header=self.client.get_request_header(),
            event_request_header=hangups.hangouts_pb2.EventRequestHeader(
                conversation_id=hangups.hangouts_pb2.ConversationId(
                    id=conversation.id
                ),
                client_generated_id=self.client.get_client_generated_id(),
            ),
            message_content=hangups.hangouts_pb2.MessageContent(
                segment=[hangups.ChatMessageSegment(message).serialize()],
            ),
        )
        try:
            yield from self.client.send_chat_message(request)
        except:
            logger.error("Unable to send message to {} with text '{}'".format(conversation, message))

if __name__ == "__main__":
    print("Run the bot using the manage.py file: ./manage.py run")
