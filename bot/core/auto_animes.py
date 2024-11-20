import os
import time
from moviepy.editor import VideoFileClip
from PIL import Image
from asyncio import gather, create_task, sleep as asleep, Event
from asyncio.subprocess import PIPE
from os import path as ospath, system
from aiofiles import open as aiopen
from aiofiles.os import remove as aioremove
from traceback import format_exc
from base64 import urlsafe_b64encode
#from time import time
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery

from bot import bot, bot_loop, Var, ani_cache, ffQueue, ffLock, ff_queued
from .tordownload import TorDownloader
from .database import db
from .func_utils import getfeed, encode, editMessage, sendMessage, convertBytes
from .text_utils import TextEditor
from .ffencoder import FFEncoder
from .tguploader import TgUploader
from .reporter import rep
from .utils import progress_for_pyrogram

btn_formatter = {
    '1080':'𝟭𝟬𝟴𝟬𝗽', 
    '720':'𝟳𝟮𝟬𝗽',
    '480':'𝟰𝟴𝟬𝗽',
    '360':'𝟯𝟲𝟬𝗽'
}

ff_encoders = {}
file_path_cache = {}

async def download_thumbnail(video, thumbnail_path="thumbnail.jpg"):
    try:
        clip = VideoFileClip(video)
        duration = clip.duration
        thumbnail_time = duration / 2
        frame = clip.get_frame(thumbnail_time)
        image = Image.fromarray(frame)
        image.save(thumbnail_path)
        clip.close()
        return thumbnail_path 
    except Exception as e:
        print(f"Error generating thumbnail: {e}")
        return None

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
        
async def fetch_animes():
    await rep.report("Fetch Animes Started !!", "info")
    while True:
        await asleep(60)
        if ani_cache['fetch_animes']:
            for link in Var.RSS_ITEMS:
                if (info := await getfeed(link, 0)):
                    bot_loop.create_task(get_animes(info.title, info.link))

@bot.on_callback_query()
async def callback_handler(client, query: CallbackQuery):
    if query.data.startswith("queue_status:"):
        encodeid = int(query.data.split(":")[1])
        position = list(ffQueue._queue).index(encodeid) + 1
        total_tasks = ffQueue.qsize()
        await query.answer(
            f"Queue Position: {position}\nTotal Queue: {total_tasks}",
            show_alert=True
        )

    elif query.data.startswith("remove_task:"):
        data = query.data.split(":")
        encodeid = int(data[1])  # The encodeid
        fpath = file_path_cache.get(encodeid)  # Retrieve the file path from cache
        
        if fpath:
            # Proceed with the removal of the task and file
            temp_queue = []
            removed = False
            while not ffQueue.empty():
                task = await ffQueue.get()
                if task == encodeid:
                    removed = True  # Mark task as removed
                    continue  # Skip this task
                temp_queue.append(task)

            # Re-add the remaining tasks back to the queue
            for task in temp_queue:
                await ffQueue.put(task)

            # Delete the file associated with the task
            if removed and os.path.exists(fpath):
                try:
                    await aioremove(fpath)  # Remove the file
                    await query.answer("Task removed from the queue and file deleted.", show_alert=True)
                except Exception as e:
                    await query.answer(f"Error deleting file: {e}", show_alert=True)
            elif removed:
                await query.answer("Task removed, but file not found.", show_alert=True)
            
            # Remove from the cache after the task is processed
            file_path_cache.pop(encodeid, None)

            # Delete the queue status message
            await query.message.delete()
        else:
            await query.answer("File path not found in cache.", show_alert=True)
    
    elif query.data.startswith("cancel_encode:"):
        # Extract the file name (encoded filename)
        encodeid = int(query.data.matches[0].group(1))

        encoder = ff_encoders.get(encodeid)
        if not encoder:
            await query.answer("Task not found or already completed!", show_alert=True)
            return
        
        
        await encoder.cancel_encode()
        ff_encoders.pop(encodeid, None)
    
        await query.message.edit_text(
            f"‣ <b>File Name :</b> <b><i>{encoder.__name}</i></b>\n\n<i>Encoding canceled by the user.</i>"
        )
        await query.answer("Encoding canceled!")


async def fencode(fname, fpath, message, m):
    await m.edit_text(
        f"File downloaded successfully:\n\n"
        f"    • <b>File Name:</b> {fname}\n"
        f"    • <b>File Path:</b> {fpath}"
    )
    stat_msg = await m.edit_text(
        f"‣ <b>File Name :</b> <b><i>{fname}</i></b>\n\n<i>Processing...</i>"
    )
    
    encodeid = m.id
    ffEvent = Event()
    ff_queued[encodeid] = ffEvent
    ff_encoders[encodeid] = None
    cancel_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Cancel", callback_data=f"cancel_encode:{encodeid}")]]
    )

    # Check if lock is active, queue task if necessary
    if ffLock.locked():
        file_path_cache[encodeid] = fpath
        queue_markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Queue Status", callback_data=f"queue_status:{encodeid}")],
                [InlineKeyboardButton("Remove from Queue", callback_data=f"remove_task:{encodeid}")]
            ]
        )
        await stat_msg.edit_text(
            f"‣ <b>File Name :</b> <b><i>{fname}</i></b>\n\n<i>Queued to Encode...</i>",
            reply_markup=queue_markup
        )

    encoder = FFEncoder(stat_msg, fpath, fname, encodeid, "360")
    ff_encoders[encodeid] = encoder

    await ffQueue.put(encodeid)
    await ffEvent.wait()
    t = time.time()

    # Acquire lock for encoding
    await ffLock.acquire()
    await stat_msg.edit_text(
        f"‣ <b>File Name :</b> <b><i>{fname}</i></b>\n\n<i>Ready to Encode...</i>",
        reply_markup=cancel_markup
    )

    await asleep(1.5)

    try:
        # Start encoding process
        out_path = await encoder.start_encode()

        # Check if the task was canceled during encoding
        if encoder.canceled:
            raise Exception("Encoding canceled by the user.")
    except Exception as e:
        await stat_msg.delete()
        ff_encoders.pop(encodeid, None)  # Remove encoder reference
        await aioremove(fpath)  # Cleanup original file
        if 'out_path' in locals() and os.path.exists(out_path):
            await aioremove(out_path)  # Cleanup output file if exists
        ffLock.release()  # Release lock
        return await message.reply(f"<b>Task Canceled or Failed: {str(e)}</b>")

    await stat_msg.edit_text("<b>Successfully Compressed. Now proceeding to upload...</b>")
    await asleep(1.5)

    try:
        # Get video info for upload
        start_time = time.time()
        duration, width, height = get_video_info(out_path)
        thumbnail_path = await download_thumbnail(out_path)
        msg = await bot.send_video(
            chat_id=message.chat.id,
            video=out_path,
            thumb=thumbnail_path,
            caption=f"‣ <b>File Name:</b> <i>{fname}</i>",
            duration=int(duration),
            width=width,
            height=height,
            supports_streaming=True,
            progress=progress_for_pyrogram,
            progress_args=("<b>Upload Started....</b>", stat_msg, start_time)
        )

        # Optional: Copy video to specific channels
        channel_ids = [-1001825550753, -1002373955828]
        for channel_id in channel_ids:
            await msg.copy(chat_id=channel_id)
    except Exception as e:
        await message.reply(
            f"<b>Error during upload: {e}. Encoding task canceled, please retry.</b>"
        )
        await stat_msg.delete()
        await aioremove(out_path)
        await aioremove(fpath)
        ffLock.release()
        return
    finally:
        await aioremove(out_path)
        await aioremove(fpath)
        if thumbnail_path:
            await aioremove(thumbnail_path)

    # Release lock and delete status message
    ffLock.release()
    await stat_msg.delete()

    # Calculate total time
    total_time = time.time() - t
    formatted_time = time.strftime("%H:%M:%S", time.gmtime(total_time))
    await message.reply(
        f"‣ <b>File Name:</b> <b><i>{fname}</i></b>\n\n"
        f"<i>Upload completed successfully.</i>\n"
        f"‣ <b>Total Time Taken:</b> {formatted_time}"
    )
