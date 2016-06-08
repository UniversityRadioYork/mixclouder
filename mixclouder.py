#Python 2 (was 3, but PIL and was designed to run on crusty debian)
import requests
import json
import argparse
import logging
import datetime
import configparser
import sys
import subprocess
import time
import re
import Image
from HTMLParser import HTMLParser

def write_demo_config(f):
    config = configparser.RawConfigParser()
    config.add_section("mixclouder")
    config.set("mixclouder", "mixcloud_client_id", "clientidgoeshere")
    config.set("mixclouder", "mixcloud_client_secret", "clientsecretgoeshere")
    config.set("mixclouder", "mixcloud_client_oauth", "useroauthtokengoeshere")
    config.set("mixclouder", "myradio_api_key", "apikeygoeshere")
    config.set("mixclouder", "myradio_url", "https://mydomain.fm/api/")
    config.set("mixclouder", "myradio_image_domain", "http://mydomain.fm/")
    config.set("mixclouder", "loggerng_url", "http://mylogger.mydomain.fm:0000/")
    config.set("mixclouder", "loggerng_memberid", 779)
    config.set("mixclouder", "loggerng_logdir", "/mnt/logs")
    config.set("mixclouder", "start_time", 0)
    config.write(f)

def myradio_api_request(url, payload={}, retry=True, method="GET"):
    payload['api_key'] = config.get("mixclouder", "myradio_api_key")
    try:
        if method == "GET":
            r = requests.get(config.get("mixclouder", "myradio_url") + url, params=payload, verify=False) # Don't verify as a hack to get it working while I sort out the ssl cert
        elif method == "POST":
            r = requests.post(config.get("mixclouder", "myradio_url") + url, params=payload, verify=False) # Don't verify as a hack to get it working while I sort out the ssl cert
    except requests.exceptions.SSLError:
        # We get these transiently. Try again.
        if retry:
            return myradio_api_request(url, payload, False)
        else:
            raise
    r = r.json() if callable (r.json) else r.json
    if r['status'] == 'OK':
        return r['payload']
    elif r['status'] == 403:
        logging.error("Server returned error 403 - The API key provided does not have access to the method %s", url)
        sys.exit()
    elif r['status'] == 401:
        logging.error("Server returned error 401 - No api key provided")
        sys.exit()
    else:
        logging.error("Unexpected server response: " + str(r) + " " + str(url) + " " + str(payload))
        sys.exit()

def get_epoch(timestamp):
  return int((datetime.datetime.strptime(timestamp+' UTC', '%d/%m/%Y %H:%M:%S %Z')-datetime.datetime(1970,1,1)).total_seconds())

def loggerng_api_request(action, timeslot):
  start_time = get_epoch(timeslot['start_time']+':00')+120
  # Timeslots return start time relevant to local time at that point. If it was in dst, subtract an hour
  if time.localtime(start_time).tm_isdst:
    start_time -= 3600
  end_time = start_time + get_epoch('01/01/1970 '+timeslot['duration'])
  return requests.get(config.get("mixclouder", "loggerng_url") + action, params={'user': config.get("mixclouder", "loggerng_memberid"), 'start': start_time, 'end': end_time, 'format': 'mp3', 'title': timeslot['timeslot_id']})

def cleanse_description(id, desc):
  # remove html tags
  desc = re.sub('<[^<]+?>', '', desc)
  # HTML unescape
  desc = HTMLParser().unescape(desc)
  # limit the length due to mixcloud api restrictions
  if len(desc) > 1000:
    desc = desc[:1000]
    # just log a warning so we can manually change if need be
    logging.warn("Timeslot %s description was too long and was trimmed", id)
  return desc

argparser = argparse.ArgumentParser(description="Takes recent shows and publishes them to Mixcloud")
argparser.add_argument('-c', '--config-file', required=True)
argparser.add_argument('--example-config', help="Write an example config file to the specified path")
args = argparser.parse_args()
logging.basicConfig(format="%(asctime)s [%(levelname)s]: %(message)s",datefmt="%d/%m/%y %H:%M:%S",level=logging.INFO)

if args.example_config:
    f = open(args.example_config, 'w')
    write_demo_config(f)
    f.close()
    sys.exit()

config = configparser.RawConfigParser()
config.read(args.config_file)

#Todo: Cross reference with mixcloud to ensure somehow isn't already there
# Logs are available for the last 14 all of those.
log_start = int(time.mktime((datetime.datetime.now() + datetime.timedelta(-14)).timetuple()))
if time.localtime(log_start).tm_isdst:
  log_start -= 3600
timeslots = []
while True:
  logging.info("Start request" + str(log_start))
  ts = myradio_api_request('Timeslot/getNextTimeslot/', {'time': log_start})
  #ts returns None if there is no next timeslot (i.e. end of term).
  if ts is None:
    break
  log_start = get_epoch(ts['start_time']+':01')
  if time.localtime(log_start).tm_isdst:
    log_start -= 3600
  logging.info("Updated Start request" + str(log_start))
  logging.info(ts['start_time'])
  logging.info(ts['mixcloud_status'])
  if log_start + get_epoch('01/01/1970 '+ts['duration']) > time.time():
    break
  #Check if this show is opted in to logging and hasn't already been done
  if ts['mixcloud_status'] == 'Requested':
    # Was something other than jukebox on air at the time? (well, 2.5m in)
    if myradio_api_request('Selector/getStudioAtTime/', {'time': log_start+150}) != 3:
      timeslots.append(ts)
      myradio_api_request('Timeslot/'+str(ts['timeslot_id'])+'/setMeta/', 
{'string_key': 'upload_state', 'value': 'Queued'}, method = "POST")
    else:
      logging.warn("Timeslot "+str(ts['timeslot_id'])+" was not on air!")
      myradio_api_request('Timeslot/'+str(ts['timeslot_id'])+'/setMeta/', {'string_key': 'upload_state', 'value': 'Skipped - Off Air'}, method = "POST")

logging.info("Found %s shows pending upload.", len(timeslots))

for timeslot in timeslots:
  #Skip ones that already have some kind of status, except queued
  if timeslot['mixcloud_status'] != 'Requested':
    logging.info("Skipping "+str(timeslot['timeslot_id'])+" as it does not need mixcloudifying.")
    continue

  tracklist = sorted(myradio_api_request('TracklistItem/getTracklistForTimeslot', {'timeslotid': timeslot['timeslot_id']}), key=lambda k: k['starttime'])
  if len(tracklist) < 1:
    logging.warn("Timeslot "+timeslot['title']+' '+str(timeslot['season_num'])+'x'+str(timeslot['timeslot_num'])+" does not have at least 1 track in its tracklist data")
    myradio_api_request('Timeslot/'+str(timeslot['timeslot_id'])+'/setMeta/', {'string_key': 'upload_state', 'value': 'Skipped - Incomplete Tracklist'}, method = "POST")
  else:
    #Great, now let's make a request for the log file
    r = loggerng_api_request("make", timeslot)
    logging.info("Initiated log generation for timeslot %s", timeslot['timeslot_id'])
    #Wait until we can download it
    r = loggerng_api_request("download", timeslot)
    while r.status_code == 403:
      logging.info("Still waiting for log generation (%s)", r.status_code)
      time.sleep(30)
      r = loggerng_api_request("download", timeslot)
    r = r.json() if callable (r.json) else r.json
    file = config.get("mixclouder", "loggerng_logdir") + '/' + r['filename_disk']
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
    
    # For the perecentage_music field, we need to work out how much is speech and how much is... well, something else.
    # Let's start with the length of the show
    duration = get_epoch('01/01/1970 '+timeslot['duration'])
    music_time = 0
    #Section Index
    sindex = 1
    # Now, for each song in the tracklist, we'll add the data to the tracklist headers, and also increment the music_time
    for i in tracklist:
      data['sections-'+str(sindex)+'-artist'] = i['artist']
      data['sections-'+str(sindex)+'-song'] = i['title']
      data['sections-'+str(sindex)+'-start_time'] = get_epoch(i['starttime'])-get_epoch(timeslot['start_time']+':00')
      sindex += 1
      music_time += get_epoch('01/01/1970 '+(str(i['length']) if i['length'] else '00:00:00'))
    # Work out that percentage of music I mentioned earlier
    data['percentage_music'] = int(music_time/duration*100)

    #Now let's open the actual file
    files = {'mp3': open(file, 'rb')}
    #Don't forget the show photo!
    r = requests.get(config.get("mixclouder", "myradio_image_domain") + timeslot['photo'])
    tmpname = "/tmp/mcphoto_"+str(timeslot['timeslot_id'])
    fp = open(tmpname, "wb")
    fp.write(r.content)
    fp.close();

    #Convert it to a square ourselves - otherwise mixcloud get special
    im = Image.open(tmpname)

    # You don't want to know what happens to alpha channels
    if im.mode == "RGBA":
      pixel_data = im.load()
      # If the image has an alpha channel, convert it to white
      # Otherwise we'll get weird pixels
      for y in xrange(im.size[1]): # For each row ...
        for x in xrange(im.size[0]): # Iterate through each column ...
          # Check if it's opaque
          if pixel_data[x, y][3] < 255:
            # Replace the pixel data with the colour white
            pixel_data[x, y] = (255, 255, 255, 255)

    xsize, ysize = im.size
    outsize = x_size if xsize > ysize else ysize
    new = Image.new("RGB", (outsize, outsize), color=(255, 255, 255))

    x1 = int(0.5*(outsize-xsize))
    y1 = int(0.5*(outsize-ysize))
    x2 = x1 + xsize
    y2 = y1 + ysize
    new.paste(im.crop((0,0,xsize,ysize)), (x1, y1, x2, y2))
    new.save(tmpname+".jpg", "JPEG")

    del im, new

    files['picture'] = open(tmpname+".jpg", "rb")
    
    logging.info("Starting upload of %s to Mixcloud", data['name'])

#    print(data)
#    print(files)

    r = requests.post('https://api.mixcloud.com/upload/?access_token='+config.get("mixclouder", "mixcloud_client_oauth"), data=data, files=files)
    info = r.json() if callable (r.json) else r.json
    if r.status_code != 200:
      logging.error(info['error']['message'])
      # Put the log back into the queue
      myradio_api_request('Timeslot/'+str(timeslot['timeslot_id'])+'/setMeta/', {'string_key': 'upload_state', 'value': 'Requested'}, method = "POST")
      # Wait before carrying on if it's an API limit
      if 'retry_after' in info['error']:
        logging.error('Waiting '+str(info['error']['retry_after'])+' seconds before continuing.')
        time.sleep(info['error']['retry_after'])
    else:
      logging.info('Upload successful!')
      myradio_api_request('Timeslot/'+str(timeslot['timeslot_id'])+'/setMeta/', {'string_key': 'upload_state', 'value': info['result']['key']}, method = "POST")
    print(r.content)
