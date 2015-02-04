import os
import shutil
import zipfile
import string
import signal
from pgmagick import Image, ImageList, Geometry, FilterTypes, Blob
from subprocess import Popen, PIPE
from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import Count
from django.utils import timezone
from openraData.models import Maps, Screenshots, Reports
from openraData import misc


def map_upgrade(mapObject, engine, http_host):
	currentDirectory = os.getcwd() + os.sep
	for item in mapObject:
		os.chdir(settings.OPENRA_PATH)
		path = currentDirectory + 'openraData/data/maps/' + str(item.id) + '/'
		filename = ""
		Dir = os.listdir(path)
		for fn in Dir:
			if fn.endswith('.oramap'):
				filename = fn
				break
		if filename == "":
			continue
		command = 'mono --debug OpenRA.Utility.exe %s --upgrade-map %s %s' % (item.game_mod, path+filename, engine)
		print(command)
		proc = Popen(command.split(), stdout=PIPE).communicate()
		os.chdir(currentDirectory)

		maphash = recalculate_hash(item)
		lint_passed = not LintCheck([item], http_host)
		Maps.objects.filter(id=item.id).update(map_hash=maphash)
		Maps.objects.filter(id=item.id).update(requires_upgrade=lint_passed)

		if not UnzipMap(item):
			print("failed to unzip %s" % item.id)
			continue
		
		read_yaml_response = ReadYaml(item)
		resp_map_data = read_yaml_response['response']
		if read_yaml_response['error']:
			print("ReadYaml: " + resp_map_data)
			continue
		else:
			Maps.objects.filter(id=item.id).update(game_mod=resp_map_data['game_mod'])
			Maps.objects.filter(id=item.id).update(title=resp_map_data['title'])
			Maps.objects.filter(id=item.id).update(author=resp_map_data['author'])
			Maps.objects.filter(id=item.id).update(tileset=resp_map_data['tileset'])
			Maps.objects.filter(id=item.id).update(map_type=resp_map_data['map_type'])
			Maps.objects.filter(id=item.id).update(description=resp_map_data['description'])
			Maps.objects.filter(id=item.id).update(players=resp_map_data['players'])
			Maps.objects.filter(id=item.id).update(bounds=resp_map_data['bounds'])
			Maps.objects.filter(id=item.id).update(mapformat=resp_map_data['mapformat'])
			Maps.objects.filter(id=item.id).update(spawnpoints=resp_map_data['spawnpoints'])
			Maps.objects.filter(id=item.id).update(width=resp_map_data['width'])
			Maps.objects.filter(id=item.id).update(height=resp_map_data['height'])
			Maps.objects.filter(id=item.id).update(shellmap=resp_map_data['shellmap'])
			Maps.objects.filter(id=item.id).update(lua=resp_map_data['lua'])
			Maps.objects.filter(id=item.id).update(advanced_map=resp_map_data['advanced'])
	os.chdir(currentDirectory)
	return True

def recalculate_hash(mapObject):
	currentDirectory = os.getcwd() + os.sep
	os.chdir(settings.OPENRA_PATH)

	path = currentDirectory + 'openraData/data/maps/' + str(mapObject.id) + '/'
	filename = ""
	Dir = os.listdir(path)
	for fn in Dir:
		if fn.endswith('.oramap'):
			filename = fn
			break
	if filename == "":
		os.chdir(currentDirectory)
		return "none"
	command = 'mono --debug OpenRA.Utility.exe %s --map-hash %s' % (mapObject.game_mod, path + filename)
	proc = Popen(command.split(), stdout=PIPE).communicate()
	maphash = proc[0].strip()
	os.chdir(currentDirectory)
	return maphash

def ReadYaml(item=False, fullpath=""):
	if fullpath == "":
		if item == False:
			return {'response': 'wrong method call', 'error': True}
		currentDirectory = os.getcwd() + os.sep
		path = currentDirectory + 'openraData/data/maps/' + str(item.id) + '/'
		Dir = os.listdir(path)
		for fn in Dir:
			if fn.endswith('.oramap'):
				fullpath = path + fn
				break
		if fullpath == "":
			return {'response': 'could not find .oramap', 'error': True}
	map_data_ordered = {}
	map_data_ordered['lua'] = False
	map_data_ordered['advanced'] = False
	map_data_ordered['players'] = 0
	map_data_ordered['description'] = ""
	map_data_ordered['shellmap'] = False

	z = zipfile.ZipFile(fullpath, mode='a')
	yamlData = ""
	for zfn in z.namelist():
		if zfn == "map.yaml":
			mapbytes = z.read(zfn)
			yamlData = mapbytes.decode("utf-8")
		if zfn.endswith('.lua'):
			map_data_ordered['lua'] = True
	z.close()
	if yamlData == "":
		return {'response': 'Failed. Invalid map format.', 'error': True}

	countAdvanced = 0
	shouldCountRules = False
	expectspawn = False
	spawnpoints = ""
	for line in string.split(yamlData, '\n'):
		if line[0:5] == "Title":
			map_data_ordered['title'] = line[6:].strip().replace("'", "''")
		if line[0:11] == "RequiresMod":
			map_data_ordered['game_mod'] = line[12:].strip().lower()
		if line[0:6] == "Author":
			map_data_ordered['author'] = line[7:].strip().replace("'", "''")
		if line[0:7] == "Tileset":
			map_data_ordered['tileset'] = line[8:].strip().lower()
		if line[0:4] == "Type":
			map_data_ordered['map_type'] = line[5:].strip()
		if line[0:11] == "Description":
			map_data_ordered['description'] = line[12:].strip().replace("'", "''")
		if line[0:7] == "MapSize":
			map_data_ordered['width'] = line[8:].strip().split(',')[0]
			map_data_ordered['height'] = line[8:].strip().split(',')[1]
		if line[0:6] == "Bounds":
			map_data_ordered['bounds'] = line[7:].strip()
		if line[0:9] == "MapFormat":
			map_data_ordered['mapformat'] = int(line[10:].strip())
		if line.strip()[-7:] == "mpspawn":
			expectspawn = True
		if line.strip()[0:8] == "Location":
			if expectspawn:
				spawnpoints += line.split(':')[1].strip()+","
				expectspawn = False
		if line.strip()[0:8] == "Playable":
			state = line.split(':')[1]
			if state.strip().lower() in ['true', 'on', 'yes', 'y']:
				map_data_ordered['players'] += 1
		if line[0:10] == "Visibility":
			if line[11:].strip() == "Shellmap":
				map_data_ordered['shellmap'] = True
		if line.strip()[0:5] == "Rules":
			shouldCountRules = True
		if shouldCountRules:
			countAdvanced += 1

	map_data_ordered['spawnpoints'] = spawnpoints.rstrip(",")
	if countAdvanced > 20:
		map_data_ordered['advanced'] = True
	if len(map_data_ordered) == 0:
		return {'response': 'map data is not filled', 'error': True}

	return {'response': map_data_ordered, 'error': False}

def UnzipMap(mapObject):
	currentDirectory = os.getcwd() + os.sep
	path = currentDirectory + 'openraData/data/maps/' + str(mapObject.id) + '/'
	filename = ""
	Dir = os.listdir(path)
	for fn in Dir:
		if fn.endswith('.oramap'):
			filename = fn
			break
	if filename == "":
		return False
	z = zipfile.ZipFile(path + filename, mode='a')
	z.extractall(path + 'content/')
	z.close()
	return True

def PushMapsToRsyncDirs():
	# this function syncs rsync directories with fresh list of maps, triggered by uploading a new map or removing one
	if settings.RSYNC_MAP_PATH.strip() == "":
		return
	mods = Maps.objects.values_list('game_mod', flat=True).distinct()
	RSYNC_MAP_PATH = misc.addSlash(settings.RSYNC_MAP_PATH)
	if os.path.exists(RSYNC_MAP_PATH):
		shutil.rmtree(RSYNC_MAP_PATH)
	for mod in mods:
		os.makedirs(RSYNC_MAP_PATH + mod.lower())
	mapObject = Maps.objects.filter(requires_upgrade=False,downloading=True,players__gte=1,rsync_allow=True,amount_reports__lt=settings.REPORTS_PENALTY_AMOUNT,next_rev=0).distinct("map_hash")
	site_path = os.getcwd() + os.sep + __name__.split('.')[0] + '/data/maps/'
	for item in mapObject:
		listd = os.listdir(site_path + str(item.id))
		for fname in listd:
			if fname.endswith('.oramap'):
				src = site_path + str(item.id) + '/' + fname
				dest_maps = RSYNC_MAP_PATH + item.game_mod.lower() + '/' + str(item.id) + '.oramap'
				os.link(src, dest_maps)
				break
		continue

def LintCheck(mapObject, http_host):
	# this function performs a Lint Check for all existing maps
	cwd = os.getcwd()
	os.chdir(settings.OPENRA_PATH)
	status = False

	for item in mapObject:
		path = cwd + os.sep + __name__.split('.')[0] + '/data/maps/' + str(item.id) + os.sep
		listdir = os.listdir(path)
		map_file = ""
		for filename in listdir:
			if filename.endswith('.oramap'):
				map_file = filename
				break
		if map_file == "":
			continue
		command = 'mono --debug OpenRA.Lint.exe ' + item.game_mod.lower() + ' ' + path + map_file
		print(command)
		proc = Popen(command.split(), stdout=PIPE).communicate()
		if proc[0].strip() == "":
			status = True
			if item.requires_upgrade:
				Maps.objects.filter(id=item.id).update(requires_upgrade=False)
		else:
			status = False
			print(proc)
			lintlog = open(path+'lintlog','w')
			lintlog.write(proc[0])
			lintlog.close()
			if not item.requires_upgrade:
				Maps.objects.filter(id=item.id).update(requires_upgrade=True)
				mail_addr = misc.return_email(item.user_id)
				if mail_addr != "":
					misc.send_email_to_user_OnLint(mail_addr, "Lint check failed for one of your maps: http://"+http_host+"/maps/"+str(item.id)+"/")
	os.chdir(cwd)
	return status

def GenerateSHPpreview(mapObject):
	# generates gif preview of shp files for every mapObject in list of objects
	currentDirectory = os.getcwd()
	for item in mapObject:
		path = os.getcwd() + os.sep + 'openraData/data/maps/' + str(item.id) + os.sep
		Dir = os.listdir(path + 'content/')
		if os.path.isdir(path+'content/png/'):
			shutil.rmtree(path+'content/png/')
		for fn in Dir:
			if fn.endswith('.shp'):
				os.mkdir(path + 'content/png/')
				os.chdir(path + 'content/png/')
				command = 'mono --debug %sOpenRA.Utility.exe %s --png %s %s' % (settings.OPENRA_PATH, item.game_mod, path+'content/'+fn, '../../../../palettes/0/RA1/temperat.pal')
				print(command)

				class TimedOut(Exception): # Raised if timed out.
					pass

				def signal_handler(signum, frame):
					raise TimedOut("Timed out!")

				signal.signal(signal.SIGALRM, signal_handler)

				signal.alarm(settings.UTILITY_TIME_LIMIT)    # Limit command execution time

				try:
					proc = Popen(command.split(), stdout=PIPE).communicate()
					signal.alarm(0)
				except:
					err = 'Error: failed to generate SHP preview for %s (map: %s)' % (fn, item.id)
					print(err)
					misc.send_email_to_admin('ORC: Failed to generate SHP preview', '%s \n\n %s' % (err, command))

					os.chdir(currentDirectory)
					shutil.rmtree(path+'content/png/')

					continue
				pngsdir = os.listdir(path + 'content/png/')
				imglist = []
				for pngfn in pngsdir:
					if pngfn.endswith('.png'):
						imglist.append(pngfn)
				imglist.sort()
				imgs = ImageList()
				for img in imglist:
					imgs.append(Image(path+'content/png/'+img))
				imgs.animationDelayImages(50)
				imgs.writeImages(path+'content/'+fn+'.gif')
				os.chdir(currentDirectory)
				shutil.rmtree(path+'content/png/')
	return True

def GenerateMinimap(mapObject):
	currentDirectory = os.getcwd() + os.sep
	os.chdir(settings.OPENRA_PATH)
	path = currentDirectory + 'openraData/data/maps/' + str(mapObject.id) + os.sep
	filename = ""
	Dir = os.listdir(path)
	for fn in Dir:
		if fn.endswith('.oramap'):
			filename = fn
			break
	if filename == "":
		os.chdir(currentDirectory)
		return False
	command = 'mono --debug OpenRA.Utility.exe %s --map-preview %s' % (mapObject.game_mod, path + filename)
	print(command)
	proc = Popen(command.split(), stdout=PIPE).communicate()
	try:
		shutil.move(settings.OPENRA_PATH + os.path.splitext(filename)[0] + ".png", path + os.path.splitext(filename)[0] + "-mini.png")
		os.chdir(currentDirectory)
		return True
	except:
		os.chdir(currentDirectory)
		return False

def GenerateFullPreview(mapObject, userObject):
	currentDirectory = os.getcwd() + os.sep
	os.chdir(settings.OPENRA_PATH)
	path = currentDirectory + 'openraData/data/maps/' + str(mapObject.id) + os.sep
	filename = ""
	Dir = os.listdir(path)
	for fn in Dir:
		if fn.endswith('.oramap'):
			filename = fn
			break
	if filename == "":
		os.chdir(currentDirectory)
		return False
	command = 'mono --debug OpenRA.Utility.exe %s --full-preview %s' % (mapObject.game_mod, path + filename)
	print(command)
	proc = Popen(command.split(), stdout=PIPE).communicate()
	try:
		shutil.move(settings.OPENRA_PATH + os.path.splitext(filename)[0] + ".png", path + os.path.splitext(filename)[0] + "-full.png")
		transac = Screenshots(
				user = userObject,
				ex_id = mapObject.id,
				ex_name = "maps",
				posted =  timezone.now(),
				map_preview = True,
		)
		transac.save()
		os.chdir(currentDirectory)
		return True
	except:
		os.chdir(currentDirectory)
		return False
