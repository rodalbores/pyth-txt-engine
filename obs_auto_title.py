import asyncio
import json
import os
import queue
from obswebsocket import obsws
from obswebsocket import requests as obs_requests

# Load video titles from JSON file
def load_video_titles():
    """Load video titles mapping from JSON file"""
    json_path = os.path.join(os.path.dirname(__file__), "video_titles.json")
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Warning: {json_path} not found. Using empty mapping.")
        return {}
    except json.JSONDecodeError as e:
        print(f"Error parsing {json_path}: {e}. Using empty mapping.")
        return {}

# Map your OBS Media Source names → Titles you want shown
# Edit video_titles.json to change the mappings without modifying code
VIDEO_TITLES = load_video_titles()

TEXT_SOURCE = "TopicTitle"        # Your Text (GDI+) source name
TEXT_SOURCE_NEXT = "TopicNextTitle"  # Your Text (GDI+) source name for next video title
HOST = "localhost"
PORT = 4455
PASSWORD = "3LRtXuY77RyYgxWv"

# Queue for thread-safe communication between event callback and main loop
# Queue items are tuples: (current_title, next_title)
update_queue = queue.Queue()


def get_next_video_title(current_video_name):
    """
    Get the next video title based on the current video name.
    Returns the next video's title, or empty string if no next video.
    """
    # Extract video number from name (e.g., "Video1" -> 1)
    try:
        # Get all video keys sorted
        video_keys = sorted(VIDEO_TITLES.keys(), key=lambda x: int(''.join(filter(str.isdigit, x))))
        
        # Find current video index
        if current_video_name in video_keys:
            current_index = video_keys.index(current_video_name)
            # Get next video (wrap around if last)
            next_index = (current_index + 1) % len(video_keys)
            next_video_name = video_keys[next_index]
            return VIDEO_TITLES[next_video_name]
    except (ValueError, IndexError) as e:
        print(f"Error finding next video: {e}")
    
    return ""


def on_event(event):
    """
    Triggered automatically every time a media source state changes
    """
    try:
        # Get event type from class name (e.g., "MediaInputPlaybackStarted")
        event_type = type(event).__name__
        
        # Only process MediaInputPlaybackStarted events
        if event_type != "MediaInputPlaybackStarted":
            return
        
        # Get event data from datain attribute
        event_data = event.datain if hasattr(event, 'datain') else {}
        
        # Get the input name from the event data
        media_name = event_data.get("inputName", "")
        
        if media_name in VIDEO_TITLES:
            new_title = VIDEO_TITLES[media_name]
            next_title = get_next_video_title(media_name)
            print(f"[UPDATE] {media_name} → {new_title} (Next: {next_title})")

            # Put the update request in the queue (thread-safe)
            # Queue item is a tuple: (current_title, next_title)
            try:
                update_queue.put((new_title, next_title), block=False)
            except queue.Full:
                print("Warning: Update queue is full, skipping update")
            except Exception as e:
                print(f"Error queuing text update: {e}")
    except Exception as e:
        print(f"Error processing event: {e}")


async def process_updates():
    """Process text updates from the queue"""
    loop = asyncio.get_event_loop()
    while True:
        try:
            # Check for updates in the queue (non-blocking)
            try:
                current_title, next_title = update_queue.get_nowait()
                
                # Update the current title text source (run in executor to avoid blocking)
                try:
                    await loop.run_in_executor(
                        None,
                        lambda: ws.call(obs_requests.SetInputSettings(
                            inputName=TEXT_SOURCE,
                            inputSettings={"text": current_title},
                            overlay=True
                        ))
                    )
                    print(f"✓ Updated {TEXT_SOURCE} to: {current_title}")
                except Exception as e:
                    print(f"Error updating {TEXT_SOURCE}: {e}")
                
                # Update the next title text source
                try:
                    await loop.run_in_executor(
                        None,
                        lambda: ws.call(obs_requests.SetInputSettings(
                            inputName=TEXT_SOURCE_NEXT,
                            inputSettings={"text": next_title},
                            overlay=True
                        ))
                    )
                    print(f"✓ Updated {TEXT_SOURCE_NEXT} to: {next_title}")
                except Exception as e:
                    print(f"Error updating {TEXT_SOURCE_NEXT}: {e}")
            except queue.Empty:
                # No updates, continue
                pass
            
            # Small delay to avoid busy waiting
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error in update processor: {e}")
            await asyncio.sleep(0.1)


async def main():
    global ws
    ws = obsws(HOST, PORT, PASSWORD)
    
    # Connect synchronously in a thread
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, ws.connect)

    # Register event handler for all events (trigger=None means catch all)
    # We'll filter for MediaInputPlaybackStarted inside the callback
    ws.eventmanager.register(on_event, None)

    print("Listening for media changes...")
    
    # Start the update processor task
    update_task = asyncio.create_task(process_updates())
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nDisconnecting...")
        update_task.cancel()
        await loop.run_in_executor(None, ws.disconnect)

asyncio.run(main())
