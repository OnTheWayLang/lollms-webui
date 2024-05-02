"""
project: lollms
file: lollms_discussion_events.py 
author: ParisNeo
description: 
    This module contains a set of Socketio routes that provide information about the Lord of Large Language and Multimodal Systems (LoLLMs) Web UI
    application. These routes are specific to discussion operation

"""
from fastapi import APIRouter, Request
from fastapi import HTTPException
from pydantic import BaseModel
import pkg_resources
from lollms.server.elf_server import LOLLMSElfServer
from fastapi.responses import FileResponse
from lollms.binding import BindingBuilder, InstallOption
from ascii_colors import ASCIIColors
from lollms.personality import MSG_TYPE, AIPersonality
from lollms.types import MSG_TYPE, SENDER_TYPES
from lollms.utilities import load_config, trace_exception, gc
from lollms.utilities import find_first_available_file_index, convert_language_name, PackageManager
from lollms_webui import LOLLMSWebUI
from pathlib import Path
from typing import List
import socketio
import threading
import os
import yaml
from lollms.databases.discussions_database import Discussion
from lollms.security import forbid_remote_access
from datetime import datetime

router = APIRouter()
lollmsElfServer = LOLLMSWebUI.get_instance()

# ----------------------------------- events -----------------------------------------
def add_events(sio:socketio):
    forbid_remote_access(lollmsElfServer)
    @sio.on('new_discussion')
    async def new_discussion(sid, data):
        if lollmsElfServer.personality is None:
            lollmsElfServer.error("Please select a personality first")
            return
        ASCIIColors.yellow("New descussion requested")
        client_id = sid
        title = data["title"]
        lollmsElfServer.session.get_client(client_id).discussion = lollmsElfServer.db.create_discussion(title)
        # Get the current timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Return a success response
        if lollmsElfServer.session.get_client(client_id).discussion is None:
            lollmsElfServer.session.get_client(client_id).discussion = lollmsElfServer.db.load_last_discussion()
    
        if lollmsElfServer.personality.welcome_message!="":
            if lollmsElfServer.personality.welcome_audio_path.exists():
                for voice in lollmsElfServer.personality.welcome_audio_path.iterdir():
                    if voice.suffix.lower() in [".wav",".mp3"]:
                            try:
                                if not PackageManager.check_package_installed("pygame"):
                                    PackageManager.install_package("pygame")
                                import pygame
                                pygame.mixer.init()
                                pygame.mixer.music.load(voice)
                                pygame.mixer.music.play()
                            except Exception as ex:
                                pass
            default_language = lollmsElfServer.personality.language.lower().strip().split()[0]
            current_language = lollmsElfServer.config.current_language.lower().strip().split()[0]

            if lollmsElfServer.config.current_language and  current_language!= default_language:
                language_path = lollmsElfServer.lollms_paths.personal_configuration_path/"personalities"/lollmsElfServer.personality.name/f"languages_{current_language}.yaml"
                if not language_path.exists():
                    lollmsElfServer.ShowBlockingMessage(f"This is the first time this personality speaks {current_language}\nLollms is reconditionning the persona in that language.\nThis will be done just once. Next time, the personality will speak {current_language} out of the box")
                    language_path.parent.mkdir(exist_ok=True, parents=True)
                    conditionning = "!@>system: "+lollmsElfServer.personality.fast_gen(f"!@>instruction: Translate the following text to {current_language}:\n{lollmsElfServer.personality.personality_conditioning.replace('!@>system:','')}\n!@>translation:\n", callback=lollmsElfServer.personality.sink)
                    welcome_message = lollmsElfServer.personality.fast_gen(f"!@>instruction: Translate the following text to {current_language}:\n{lollmsElfServer.personality.welcome_message}\n!@>translation:\n", callback=lollmsElfServer.personality.sink)
                    with open(language_path,"w",encoding="utf-8", errors="ignore") as f:
                        yaml.safe_dump({"conditionning":conditionning,"welcome_message":welcome_message}, f)
                    lollmsElfServer.HideBlockingMessage()
                else:
                    with open(language_path,"r",encoding="utf-8", errors="ignore") as f:
                        language_pack = yaml.safe_load(f)
                        welcome_message = language_pack["welcome_message"]
            else:
                welcome_message = lollmsElfServer.personality.welcome_message

            try:
                nb_tokens = len(lollmsElfServer.model.tokenize(welcome_message))
            except:
                nb_tokens = None
            message = lollmsElfServer.session.get_client(client_id).discussion.add_message(
                message_type        = MSG_TYPE.MSG_TYPE_FULL.value if lollmsElfServer.personality.include_welcome_message_in_discussion else MSG_TYPE.MSG_TYPE_FULL_INVISIBLE_TO_AI.value,
                sender_type         = SENDER_TYPES.SENDER_TYPES_AI.value,
                sender              = lollmsElfServer.personality.name,
                content             = welcome_message,
                metadata            = None,
                rank                = 0, 
                parent_message_id   = -1, 
                binding             = lollmsElfServer.config.binding_name, 
                model               = lollmsElfServer.config.model_name,
                personality         = lollmsElfServer.config.personalities[lollmsElfServer.config.active_personality_id], 
                created_at          = None, 
                started_generating_at   = None,
                finished_generating_at  = None,
                nb_tokens     = nb_tokens
            )

            await lollmsElfServer.sio.emit('discussion_created',
                        {'id':lollmsElfServer.session.get_client(client_id).discussion.discussion_id},
                        to=client_id
            )                        
        else:
            await lollmsElfServer.sio.emit('discussion_created',
                        {'id':0},
                        to=client_id
            )                        

    @sio.on('load_discussion')
    async def load_discussion(sid, data):   
        client_id = sid
        ASCIIColors.yellow(f"Loading discussion for client {client_id} ... ", end="")
        if "id" in data:
            discussion_id = data["id"]
            lollmsElfServer.session.get_client(client_id).discussion = Discussion(lollmsElfServer, discussion_id, lollmsElfServer.db)
        else:
            if lollmsElfServer.session.get_client(client_id).discussion is not None:
                discussion_id = lollmsElfServer.session.get_client(client_id).discussion.discussion_id
                lollmsElfServer.session.get_client(client_id).discussion = Discussion(lollmsElfServer, discussion_id, lollmsElfServer.db)
            else:
                lollmsElfServer.session.get_client(client_id).discussion = lollmsElfServer.db.create_discussion()
        messages = lollmsElfServer.session.get_client(client_id).discussion.get_messages()
        jsons = [m.to_json() for m in messages]
        await lollmsElfServer.sio.emit('discussion',
                    jsons,
                    to=client_id
        )
        ASCIIColors.green(f"ok")