import os
import time
import asyncio
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

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

@bot.on_callback_query()
async def callback_query_handler(client, callback_query):
    data = callback_query.data

    if data.startswith("check_queue:"):
        encodeid = data.split(":")[1]  # Extract the encode ID from callback data

        # Calculate real-time queue position
        queue_list = list(ffQueue._queue)  # Current queue state
        total_queue = len(queue_list)  # Total tasks in the queue

        if encodeid in queue_list:
            # Task is still in the queue
            queue_position = queue_list.index(encodeid) + 1  # 1-based index
            position_message = f"Queue Position: {queue_position}\nTotal Queue: {total_queue}"
        elif encodeid in ongoing_tasks:
            # Task is ongoing (currently being processed)
            position_message = f"Queue Position: Ongoing\nTotal Queue: {total_queue}"
        else:
            # The task has either been completed or removed from the queue
            position_message = "This task is no longer in the queue."

        # Show popup with the position details
        await callback_query.answer(position_message, show_alert=True)


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

# Global ongoing tasks tracker (to track currently processing files)
ongoing_tasks = {}

async def fencode(fname, fpath, message, m):
    # Notify the user that encoding has started
    encode = await m.edit_text(
        f"File downloaded successfully:\n\n"
        f"    • <b>File Name:</b> {fname}\n"
        f"    • <b>File Path:</b> {fpath}"
    )

    stat_msg = await encode.edit_text(
        f"‣ <b>File Name :</b> <b><i>{fname}</i></b>\n\n<i>Processing...</i>",
    )

    encodeid = encode.id
    ffEvent = Event()
    ff_queued[encodeid] = ffEvent

    # If the lock is already engaged, inform the user that the task is queued
    if ffLock.locked():
        check_queue_markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Check Queue", callback_data=f"check_queue:{encodeid}"
                    )
                ]
            ]
        )
        await stat_msg.edit_text(
            f"‣ <b>File Name :</b> <b><i>{fname}</i></b>\n\n"
            f"<i>Queued to Encode...</i>",
            reply_markup=check_queue_markup,
        )

    # Add the encoding task to the queue and wait for its turn
    await ffQueue.put(encodeid)
    await ffEvent.wait()
    t = time.time()

    # Mark this file as ongoing
    ongoing_tasks[encodeid] = True

    # Acquire the lock for the current encoding task
    await ffLock.acquire()
    await stat_msg.edit_text(
        f"‣ <b>File Name :</b> <b><i>{fname}</i></b>\n\n<i>Ready to Encode...</i>"
    )

    await asleep(1.5)

    try:
        # Start the encoding process
        out_path = await FFEncoder(stat_msg, fpath, fname, "360").start_encode()
    except Exception as e:
        await stat_msg.delete()
        ffLock.release()
        del ongoing_tasks[encodeid]
        return await message.reply(f"<b>Encoding failed: {str(e)}</b>")

    await stat_msg.edit_text("<b>Successfully Compressed. Now proceeding to upload...</b>")
    await asleep(1.5)

    try:
        start_time = time.time()
        duration, width, height = get_video_info(out_path)
        thumbnail_path = await download_thumbnail(out_path)

        # Upload the encoded file
        await bot.send_video(
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
    except Exception as e:
        await message.reply(
            f"<b>Error during upload: {e}. Encoding task canceled, please retry.</b>"
        )
        await stat_msg.delete()
        ffLock.release()
        del ongoing_tasks[encodeid]
        return
    finally:
        await aioremove(out_path)
        await aioremove(thumbnail_path)

    # Release the lock once the task is completed
    ffLock.release()
    del ongoing_tasks[encodeid]
    await stat_msg.delete()
    total_time = time.time() - t
    formatted_time = time.strftime("%H:%M:%S", time.gmtime(total_time))
    await message.reply(
        f"‣ <b>File Name:</b> <b><i>{fname}</i></b>\n\n"
        f"<i>Upload completed successfully.</i>\n"
        f"‣ <b>Total Time Taken:</b> {formatted_time}"
    )




async def get_animes(name, torrent, force=False):
    try:
        aniInfo = TextEditor(name)
        await aniInfo.load_anilist()
        ani_id, ep_no = aniInfo.adata.get('id'), aniInfo.pdata.get("episode_number")
        if ani_id not in ani_cache['ongoing']:
            ani_cache['ongoing'].add(ani_id)
        elif not force:
            return
        if not force and ani_id in ani_cache['completed']:
            return
        if force or (not (ani_data := await db.getAnime(ani_id)) \
            or (ani_data and not (qual_data := ani_data.get(ep_no))) \
            or (ani_data and qual_data and not all(qual for qual in qual_data.values()))):
            
            if "[Batch]" in name:
                await rep.report(f"Torrent Skipped!\n\n{name}", "warning")
                return
            
            await rep.report(f"New Anime Torrent Found!\n\n{name}", "info")
            post_msg = await bot.send_photo(
                Var.MAIN_CHANNEL,
                photo=await aniInfo.get_poster(),
                caption=await aniInfo.get_caption()
            )
            #post_msg = await sendMessage(Var.MAIN_CHANNEL, (await aniInfo.get_caption()).format(await aniInfo.get_poster()), invert_media=True)
            
            await asleep(1.5)
            stat_msg = await sendMessage(Var.MAIN_CHANNEL, f"‣ <b>Anime Name :</b> <b><i>{name}</i></b>\n\n<i>Downloading...</i>")
            await rep.report(f"The Torrent Link Was!\n\n{torrent}", "info")
            dl = await TorDownloader("./downloads").download(torrent, name)
            if not dl or not ospath.exists(dl):
                await rep.report(f"File Download Incomplete, Try Again", "error")
                await stat_msg.delete()
                return

            post_id = post_msg.id
            ffEvent = Event()
            ff_queued[post_id] = ffEvent
            if ffLock.locked():
                await editMessage(stat_msg, f"‣ <b>Anime Name :</b> <b><i>{name}</i></b>\n\n<i>Queued to Encode...</i>")
                await rep.report("Added Task to Queue...", "info")
            await ffQueue.put(post_id)
            await ffEvent.wait()
            
            await ffLock.acquire()
            btns = []
            for qual in Var.QUALS:
                filename = await aniInfo.get_upname(qual)
                await editMessage(stat_msg, f"‣ <b>Anime Name :</b> <b><i>{name}</i></b>\n\n<i>Ready to Encode...</i>")
                
                await asleep(1.5)
                await rep.report("Starting Encode...", "info")
                try:
                    out_path = await FFEncoder(stat_msg, dl, filename, qual).start_encode()
                except Exception as e:
                    await rep.report(f"Error: {e}, Cancelled,  Retry Again !", "error")
                    await stat_msg.delete()
                    ffLock.release()
                    return
                await rep.report("Succesfully Compressed Now Going To Upload...", "info")
                
                await editMessage(stat_msg, f"‣ <b>Anime Name :</b> <b><i>{filename}</i></b>\n\n<i>Ready to Upload...</i>")
                await asleep(1.5)
                try:
                    msg = await TgUploader(stat_msg).upload(out_path, qual)
                except Exception as e:
                    await rep.report(f"Error: {e}, Cancelled,  Retry Again !", "error")
                    await stat_msg.delete()
                    ffLock.release()
                    return
                await rep.report("Succesfully Uploaded File into Tg...", "info")
                
                msg_id = msg.id
                link = f"https://telegram.me/{(await bot.get_me()).username}?start={await encode('get-'+str(msg_id * abs(Var.FILE_STORE)))}"
                
                if post_msg:
                    if len(btns) != 0 and len(btns[-1]) == 1:
                        btns[-1].insert(1, InlineKeyboardButton(f"{btn_formatter[qual]} - {convertBytes(msg.document.file_size)}", url=link))
                    else:
                        btns.append([InlineKeyboardButton(f"{btn_formatter[qual]} - {convertBytes(msg.document.file_size)}", url=link)])
                    await editMessage(post_msg, post_msg.caption.html if post_msg.caption else "", InlineKeyboardMarkup(btns))
                    
                await db.saveAnime(ani_id, ep_no, qual, post_id)
                bot_loop.create_task(extra_utils(msg_id, out_path))
            ffLock.release()
            
            await stat_msg.delete()
            await aioremove(dl)
        ani_cache['completed'].add(ani_id)
    except Exception as error:
        await rep.report(format_exc(), "error")

async def extra_utils(msg_id, out_path):
    msg = await bot.get_messages(Var.FILE_STORE, message_ids=msg_id)

    if Var.BACKUP_CHANNEL != 0:
        for chat_id in Var.BACKUP_CHANNEL.split():
            await msg.copy(int(chat_id))
            
    # MediaInfo, ScreenShots, Sample Video ( Add-ons Features )
