import argparse
import configparser
import datetime
import html
import json
import logging
from PIL import Image
import re
import requests
import sys
import time

def write_demo_config(f):
    config = configparser.RawConfigParser()
    config.add_section("mixclouder")
    config.set("mixclouder", "mixcloud_client_id", "clientidgoeshere")
    config.set("mixclouder", "mixcloud_client_secret", "clientsecretgoeshere")
    config.set("mixclouder", "mixcloud_client_oauth", "useroauthtokengoeshere")
    config.set("mixclouder", "myradio_api_key", "apikeygoeshere")
    config.set("mixclouder", "myradio_url", "https://mydomain.fm/api/")
    config.set("mixclouder", "myradio_image_domain", "http://mydomain.fm/")
    config.set("mixclouder", "loggerng_url", "http://logger.mydomain.fm:0000/")
    config.set("mixclouder", "loggerng_memberid", 779)
    config.set("mixclouder", "loggerng_logdir", "/mnt/logs")
    config.set("mixclouder", "start_time", 0)
    config.set("mixclouder", "news_length", 120)  # news length offset (secs)
    config.write(f)


def myradio_api_request(url, payload=None, retry=True, method="GET"):
    if payload is None:
        payload = {}
    payload['api_key'] = config.get("mixclouder", "myradio_api_key")
    if method == "GET":
        req_func = requests.get
    elif method == "POST":
        req_func = requests.post
    r = req_func(config.get("mixclouder", "myradio_url") + url, params=payload)
    r = r.json()
    if r['status'] == 'OK':
        return r['payload']
    elif r['status'] == 403:
        logging.error("Server returned error 403 - The API key provided does "
                      "not have access to the method %s", url)
        sys.exit()
    elif r['status'] == 401:
        logging.error("Server returned error 401 - No api key provided")
        sys.exit()
    else:
        logging.error("Unexpected server response: %s %s %s", r, url, payload)
        sys.exit()


def get_epoch(timestamp):
    unix = datetime.datetime.strptime(timestamp+' UTC', '%d/%m/%Y %H:%M:%S %Z')
    return int((unix - datetime.datetime(1970, 1, 1)).total_seconds())


def get_duration(duration):
    # Can't use strptime in the case that the show is >24 hours
    # (Yes this does happen occasionally)
    hours, minutes, seconds = map(int, duration.split(':'))
    td = datetime.timedelta(hours=hours, minutes=minutes, seconds=seconds)
    return int(td.total_seconds())

def checkCustomTimes(timeslot):
    if timeslot['mixcloud_starttime'] == None:
        start_time = get_epoch(timeslot['start_time']+':00') + int(config.get("mixclouder", "news_length"))
    else:
        start_time = get_epoch(timeslot['mixcloud_starttime']+':00')
    # Timeslots return start time relevant to local time at that point.
    # If it was in dst, subtract an hour
    if time.localtime(start_time).tm_isdst:
        start_time -= 3600

    # Calculate end of mixcloud recording
    if timeslot['mixcloud_endtime'] != None:
        # Custom End Time is defined

        end_time = get_epoch(timeslot['mixcloud_endtime']+':00')
        if time.localtime(end_time).tm_isdst:
                end_time -= 3600

    elif timeslot['mixcloud_starttime'] != None:
        # No custom end time, so end time is the original scheduled start time + original duration

        original_start_time = get_epoch(timeslot['start_time']+':00') + int(config.get("mixclouder", "news_length"))
        if time.localtime(original_start_time).tm_isdst:
                original_start_time -= 3600
        end_time = original_start_time + get_duration(timeslot['duration'])

    else:
        # Non-custom start and end time, just use regular start time and duration from schedule
        end_time = start_time + get_duration(timeslot['duration'])

    duration = datetime.datetime.fromtimestamp(end_time - start_time).strftime('%H:%M:%S')
    timeslot['start_time_epoch'] = start_time
    timeslot['end_time_epoch'] = end_time
    timeslot['duration'] = duration
    return timeslot
    

def loggerng_api_request(action, timeslot):
    params = {
        'user': config.get("mixclouder", "loggerng_memberid"),
        'start': timeslot['start_time_epoch'],
        'end': timeslot['end_time_epoch'],
        'format': 'mp3',
        'title': timeslot['timeslot_id']
    }
    return requests.get(config.get("mixclouder", "loggerng_url") + action,
                        params=params)


def cleanse_description(id, desc):
    # remove html tags
    desc = re.sub('<[^<]+?>', '', desc)
    # HTML unescape
    desc = html.unescape(desc)
    # limit the length due to mixcloud api restrictions
    if len(desc) > 1000:
        desc = desc[:1000]
        # just log a warning so we can manually change if need be
        logging.warn("Timeslot %s description was too long and was trimmed", id)
    return desc


argparser = argparse.ArgumentParser(
        description="Takes recent shows and publishes them to Mixcloud")
argparser.add_argument('-c', '--config-file', required=True)
argparser.add_argument('--example-config',
                       help="Write example config file to the specified path")
args = argparser.parse_args()
logging.basicConfig(format="%(asctime)s [%(levelname)s]: %(message)s",
                    datefmt="%d/%m/%y %H:%M:%S", level=logging.INFO)

if args.example_config:
    f = open(args.example_config, 'w')
    write_demo_config(f)
    f.close()
    sys.exit()

config = configparser.RawConfigParser()
config.read(args.config_file)

# TODO: Cross reference with mixcloud to ensure somehow isn't already there
# Logs are available for the last 14 all of those.
log_start = int(time.mktime((datetime.datetime.now() + datetime.timedelta(-14)).timetuple()))
if time.localtime(log_start).tm_isdst:
    log_start -= 3600
timeslots = []
while True:
    logging.info("Start request %s", log_start)
    ts = myradio_api_request('Timeslot/getNextTimeslot/', {'time': log_start})
    # ts returns None if there is no next timeslot (i.e. end of term).
    if ts is None:
        break
    log_start = get_epoch(ts['start_time']+':01')
    if time.localtime(log_start).tm_isdst:
        log_start -= 3600
    logging.info("Updated Start request %s", log_start)
    logging.info(ts['start_time'])
    logging.info(ts['mixcloud_status'])
    if log_start + get_duration(ts['duration']) > time.time():
        break
    if ts['mixcloud_status'] == 'Queued':
    	timeslots.append(ts)

    # Check if we want to force upload this anyway.
    if ts['mixcloud_status'] in ['Force Upload', 'Played Out']:
        timeslots.append(ts)
        myradio_api_request('Timeslot/'+str(ts['timeslot_id'])+'/setMeta/',
                                {'string_key': 'upload_state', 'value': 'Queued'},
                                method="POST")

    # Check if this show is opted in to logging and hasn't already been done
    if ts['mixcloud_status'] == 'Requested':
        # Was something other than jukebox on air at the time? (well, 5m in)
        studio_on_air = myradio_api_request('Selector/getStudioAtTime/',
                                            {'time': log_start+300})
        if studio_on_air != 3:
            timeslots.append(ts)
            myradio_api_request('Timeslot/'+str(ts['timeslot_id'])+'/setMeta/',
                                {'string_key': 'upload_state', 'value': 'Queued'},
                                method="POST")
        else:
            logging.warn("Timeslot %s was not on air!", ts['timeslot_id'])
            myradio_api_request('Timeslot/'+str(ts['timeslot_id'])+'/setMeta/',
                                {'string_key': 'upload_state', 'value': 'Skipped - Off Air'},
                                method="POST")

logging.info("Found %s shows pending upload.", len(timeslots))

for timeslot in timeslots:
    # Skip ones that already have some kind of status, except queued
    if timeslot['mixcloud_status'] != 'Requested' and  timeslot['mixcloud_status'] != 'Force Upload':
        logging.info("Skipping %s as it does not need mixcloudifying.",
                     timeslot['timeslot_id'])
        continue

    tracklist = sorted(myradio_api_request('TracklistItem/getTracklistForTimeslot',
                                           {'timeslotid': timeslot['timeslot_id']}),
                       key=lambda k: k['starttime'])
    if len(tracklist) < 1:
        logging.warn("Timeslot %s %sx%s does not have at least 1 track in its tracklist data",
                     timeslot['title'], timeslot['season_num'],
                     timeslot['timeslot_num'])
        myradio_api_request('Timeslot/'+str(timeslot['timeslot_id'])+'/setMeta/',
                            {'string_key': 'upload_state', 'value': 'Skipped - Incomplete Tracklist'},
                            method="POST")
        continue
    timeslot = checkCustomTimes(timeslot)
    print(timeslot)

    # Great, now let's make a request for the log file
    r = loggerng_api_request("make", timeslot)
    logging.info("Initiated log generation for timeslot %s",
                 timeslot['timeslot_id'])

    # Wait until we can download it
    r = loggerng_api_request("download", timeslot)
    while r.status_code == 403:
        logging.info("Still waiting for log generation (%s)", r.status_code)
        time.sleep(30)
        r = loggerng_api_request("download", timeslot)
    r = r.json()
    audiofile = config.get("mixclouder", "loggerng_logdir") + '/' + r['filename_disk']
    # Okay, time to build request data
    data = {
        "name": timeslot['title'] + ' ' + time.strftime('%d/%m/%Y', time.localtime(get_epoch(timeslot['start_time'] + ':00'))),
        "description": cleanse_description(timeslot['timeslot_id'], timeslot['description']),
        'sections-0-start_time': 0,
        'sections-0-chapter': 'Top of Hour'
    }

    # Add the tags, if they are a thing
    for i in range(min(5, len(timeslot['tags']))):
        data['tags-'+str(i)+'-tag'] = timeslot['tags'][i]

    # For the percentage_music field, we need to work out how much is speech
    # and how much is... well, something else.
    # Let's start with the length of the show
    duration = get_duration(timeslot['duration'])
    music_time = 0
    # Section Index
    sindex = 1
    # Now, for each song in the tracklist, we'll add the data to the tracklist
    # headers, and also increment the music_time
    for i in tracklist:
        if (get_epoch(i['starttime']) - timeslot['start_time_epoch'] >= 0):
            data['sections-' + str(sindex) + '-artist'] = i['artist']
            data['sections-' + str(sindex) + '-song'] = i['title']
            data['sections-' + str(sindex) + '-start_time'] = i['time'] - timeslot['start_time_epoch']
            sindex += 1
            music_time += get_duration(str(i['length'])) if i['length'] else 0

    # Work out that percentage of music I mentioned earlier
    data['percentage_music'] = int(music_time/duration*100)

    # Don't forget the show photo!
    r = requests.get(config.get("mixclouder", "myradio_image_domain") + timeslot['photo'])
    tmpname = "/tmp/mcphoto_"+str(timeslot['timeslot_id'])
    fp = open(tmpname, "wb")
    fp.write(r.content)
    fp.close()

    im = Image.open(tmpname).convert('RGBA')

    # If the image has an alpha channel, convert it to white
    # Otherwise we'll get weird pixels
    background = Image.new('RGBA', im.size, (255,255,255))
    im = Image.alpha_composite(background, im)

    # Convert it to a square ourselves - otherwise mixcloud get special
    xsize, ysize = im.size
    outsize = max(xsize, ysize)
    new = Image.new("RGB", (outsize, outsize), color=(255, 255, 255))

    x1 = int(0.5*(outsize-xsize))
    y1 = int(0.5*(outsize-ysize))
    x2 = x1 + xsize
    y2 = y1 + ysize
    new.paste(im.crop((0, 0, xsize, ysize)), (x1, y1, x2, y2))
    new.save(tmpname+".jpg", "JPEG")

    # Now let's open the actual file
    files = {
        'mp3': open(audiofile, 'rb'),
        'picture': open(tmpname+'.jpg', 'rb')
    }

    logging.info("Starting upload of %s to Mixcloud", data['name'])
    r = requests.post('https://api.mixcloud.com/upload/?access_token='+config.get("mixclouder", "mixcloud_client_oauth"), data=data, files=files)
    try:
    	info = r.json()
    except:
        logging.error("API response not JSON")
        logging.error(r)
	# Put the log back into the queue
        myradio_api_request('Timeslot/'+str(timeslot['timeslot_id'])+'/setMeta/', {'string_key': 'upload_state', 'value': 'Requested'}, method="POST")
        continue

    if r.status_code != 200:
        logging.error(info)
        # Put the log back into the queue
        #myradio_api_request('Timeslot/'+str(timeslot['timeslot_id'])+'/setMeta/', {'string_key': 'upload_state', 'value': 'Requested'}, method="POST")
        # Wait before carrying on if it's an API limit
        if 'retry_after' in info['error']:
            logging.error('Waiting %s seconds before continuing',
                          info['error']['retry_after'])
            time.sleep(info['error']['retry_after'])
    else:
        logging.info('Upload successful!')
        myradio_api_request('Timeslot/'+str(timeslot['timeslot_id'])+'/setMeta/',
                            {'string_key': 'upload_state', 'value': info['result']['key']},
                            method="POST")
    print(r.json())
