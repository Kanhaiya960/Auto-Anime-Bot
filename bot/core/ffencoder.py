from re import findall 
from math import floor
from time import time
from os import path as ospath
from aiofiles import open as aiopen
from aiofiles.os import remove as aioremove, rename as aiorename
from shlex import split as ssplit
from asyncio import sleep as asleep, gather, create_subprocess_shell, create_task
from asyncio.subprocess import PIPE
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from moviepy.editor import VideoFileClip
from PIL import Image

from bot import Var, bot_loop, ffpids_cache, LOGS
from .func_utils import mediainfo, convertBytes, convertTime, sendMessage, editMessage
from .reporter import rep
#from .auto_animes import get_video_info

ffargs = {
    '1080': Var.FFCODE_1080,
    '720': Var.FFCODE_720,
    '480': Var.FFCODE_480,
    '360': Var.FFCODE_360,
    '361': Var.FFCODE_361,
}

def get_video_info(video_path):
    try:
        clip = VideoFileClip(video_path)
        duration = clip.duration  # Duration in seconds
        width, height = clip.size  # Video resolution
        clip.close()
        print("Video information retrieved successfully!")
        return duration, width, height
    except Exception as e:
        print(f"Error getting video info: {e}")
        return None, None, None

class FFEncoder:
    def __init__(self, message, path, name, encodeid, qual):
        self.__proc = None
        self.is_cancelled = False
        self.message = message
        self.__name = name
        self.__qual = qual
        self.dl_path = path
        self.__total_time = None
        self.out_path = ospath.join("encode", name)
        self.__prog_file = 'prog.txt'
        self.__start_time = time()
        self.__encodeid = encodeid

        LOGS.info(f"Initialized FFEncoder with file: {self.__name}, quality: {self.__qual}")

    async def progress(self):
        LOGS.info(f"Retrieving video information for {self.__name}")
        self.__total_time, _, _ = get_video_info(self.dl_path)
        LOGS.info(f"Video duration: {self.__total_time} seconds")
        if isinstance(self.__total_time, str):
            self.__total_time = 1.0
        LOGS.info(f"Video total duration: {self.__total_time} seconds")
        
        while not (self.__proc is None or self.is_cancelled):
            async with aiopen(self.__prog_file, 'r+') as p:
                text = await p.read()
            if text:
                time_done = floor(int(t[-1]) / 1000000) if (t := findall("out_time_ms=(\d+)", text)) else 1
                ensize = int(s[-1]) if (s := findall(r"total_size=(\d+)", text)) else 0
                
                diff = time() - self.__start_time
                speed = ensize / diff
                percent = round((time_done/self.__total_time)*100, 2)
                tsize = ensize / (max(percent, 0.01)/100)
                eta = (tsize-ensize)/max(speed, 0.01)
    
                bar = floor(percent/8)*"█" + (12 - floor(percent/8))*"▒"
                
                progress_str = f"""<blockquote>‣ <b>File Name :</b> <b><i>{self.__name}</i></b></blockquote>
<blockquote>‣ <b>Status :</b> <i>Encoding</i>
    <code>[{bar}]</code> {percent}%</blockquote> 
<blockquote>   ‣ <b>Size :</b> {convertBytes(ensize)} out of ~ {convertBytes(tsize)}
    ‣ <b>Speed :</b> {convertBytes(speed)}/s
    ‣ <b>Time Took :</b> {convertTime(diff)}
    ‣ <b>Time Left :</b> {convertTime(eta)}</blockquote>"""
                cancel_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Cancel Encoding", callback_data=f"cancel_encoding:{self.__encodeid}")]
                ])
                await editMessage(self.message, progress_str)
                
                if (prog := findall(r"progress=(\w+)", text)) and prog[-1] == 'end':
                    LOGS.info(f"Encoding of {self.__name} completed.")
                    break

            await asleep(8)
    
    async def start_encode(self):
        LOGS.info(f"Starting encoding for file: {self.__name}")
        
        if ospath.exists(self.__prog_file):
            await aioremove(self.__prog_file)
            LOGS.info(f"Removed existing progress file: {self.__prog_file}")
    
        async with aiopen(self.__prog_file, 'w+'):
            LOGS.info("Progress Temp Generated!")
            pass

        dl_npath, out_npath = ospath.join("encode", "ffanimeadvin.mkv"), ospath.join("encode", "ffanimeadvout.mkv")
        await aiorename(self.dl_path, dl_npath)
        LOGS.info(f"Renamed downloaded file to: {dl_npath}")

        ffcode = ffargs[self.__qual].format(dl_npath, self.__prog_file, out_npath)
        LOGS.info(f'FFCode: {ffcode}')
        
        self.__proc = await create_subprocess_shell(ffcode, stdout=PIPE, stderr=PIPE)
        proc_pid = self.__proc.pid
        ffpids_cache.append(proc_pid)
        LOGS.info(f"Started encoding process with PID: {proc_pid}")
        
        _, return_code = await gather(create_task(self.progress()), self.__proc.wait())
        ffpids_cache.remove(proc_pid)
        
        await aiorename(dl_npath, self.dl_path)
        LOGS.info(f"Restored original file name: {self.dl_path}")
        
        if self.is_cancelled:
            LOGS.info("Encoding process was cancelled.")
            return
        
        if return_code == 0:
            if ospath.exists(out_npath):
                await aiorename(out_npath, self.out_path)
                LOGS.info(f"Encoding successful. Output file: {self.out_path}")
            return self.out_path
        else:
            error_message = await self.__proc.stderr.read()
            LOGS.error(f"Encoding failed for {self.__name}. Error: {error_message.decode().strip()}")
            await rep.report(error_message.decode().strip(), "error")
            
    async def cancel_encode(self):
        self.is_cancelled = True
        if self.__proc is not None:
            try:
                self.__proc.kill()
                LOGS.info(f"Encoding process for {self.__name} was terminated.")
            except:
                pass
