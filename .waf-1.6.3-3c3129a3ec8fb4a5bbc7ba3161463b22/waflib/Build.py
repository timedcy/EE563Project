#! /usr/bin/env python
# encoding: utf-8
# WARNING! All changes made to this file will be lost!

import os,sys,errno,re,datetime,shutil
try:import cPickle
except:import pickle as cPickle
from waflib import Runner,TaskGen,Utils,ConfigSet,Task,Logs,Options,Context,Errors
import waflib.Node
CACHE_DIR='c4che'
CACHE_SUFFIX='_cache.py'
INSTALL=1337
UNINSTALL=-1337
SAVED_ATTRS='root node_deps raw_deps task_sigs'.split()
CFG_FILES='cfg_files'
POST_AT_ONCE=0
POST_LAZY=1
POST_BOTH=2
class BuildContext(Context.Context):
	'''executes the build'''
	cmd='build'
	variant=''
	def __init__(self,**kw):
		super(BuildContext,self).__init__(**kw)
		self.is_install=0
		self.top_dir=kw.get('top_dir',Context.top_dir)
		self.run_dir=kw.get('run_dir',Context.run_dir)
		self.post_mode=POST_AT_ONCE
		self.out_dir=kw.get('out_dir',Context.out_dir)
		self.cache_dir=kw.get('cache_dir',None)
		if not self.cache_dir:
			self.cache_dir=self.out_dir+os.sep+CACHE_DIR
		self.all_envs={}
		self.task_sigs={}
		self.node_deps={}
		self.raw_deps={}
		self.cache_dir_contents={}
		self.task_gen_cache_names={}
		self.launch_dir=Context.launch_dir
		self.jobs=Options.options.jobs
		self.targets=Options.options.targets
		self.keep=Options.options.keep
		self.cache_global=Options.cache_global
		self.nocache=Options.options.nocache
		self.progress_bar=Options.options.progress_bar
		self.deps_man=Utils.defaultdict(list)
		self.current_group=0
		self.groups=[]
		self.group_names={}
	def get_variant_dir(self):
		if not self.variant:
			return self.out_dir
		return os.path.join(self.out_dir,self.variant)
	variant_dir=property(get_variant_dir,None)
	def __call__(self,*k,**kw):
		kw['bld']=self
		ret=TaskGen.task_gen(*k,**kw)
		self.task_gen_cache_names={}
		self.add_to_group(ret,group=kw.get('group',None))
		return ret
	def __copy__(self):
		raise Errors.WafError('build contexts are not supposed to be copied')
	def install_files(self,*k,**kw):
		pass
	def install_as(self,*k,**kw):
		pass
	def symlink_as(self,*k,**kw):
		pass
	def load_envs(self):
		try:
			lst=Utils.listdir(self.cache_dir)
		except OSError ,e:
			if e.errno==errno.ENOENT:
				raise Errors.WafError('The project was not configured: run "waf configure" first!')
			else:
				raise
		if not lst:
			raise Errors.WafError('The cache directory is empty: reconfigure the project')
		for fname in lst:
			if fname.endswith(CACHE_SUFFIX):
				env=ConfigSet.ConfigSet(os.path.join(self.cache_dir,fname))
				name=fname[:-len(CACHE_SUFFIX)]
				self.all_envs[name]=env
				for f in env[CFG_FILES]:
					newnode=self.root.find_resource(f)
					try:
						h=Utils.h_file(newnode.abspath())
					except(IOError,AttributeError):
						Logs.error('cannot find %r'%f)
						h=Utils.SIG_NIL
					newnode.sig=h
	def init_dirs(self):
		if not(os.path.isabs(self.top_dir)and os.path.isabs(self.out_dir)):
			raise Errors.WafError('The project was not configured: run "waf configure" first!')
		self.path=self.srcnode=self.root.find_dir(self.top_dir)
		self.bldnode=self.root.make_node(self.variant_dir)
		self.bldnode.mkdir()
	def execute(self):
		self.restore()
		if not self.all_envs:
			self.load_envs()
		self.execute_build()
	def execute_build(self):
		Logs.info("Waf: Entering directory `%s'"%self.variant_dir)
		self.recurse([self.run_dir])
		self.pre_build()
		self.timer=Utils.Timer()
		if self.progress_bar:
			sys.stderr.write(Logs.colors.cursor_off)
		try:
			self.compile()
		finally:
			if self.progress_bar==1:
				c=len(self.returned_tasks)or 1
				self.to_log(self.progress_line(c,c,Logs.colors.BLUE,Logs.colors.NORMAL))
				sys.stderr.write(Logs.colors.cursor_on)
				print('')
			Logs.info("Waf: Leaving directory `%s'"%self.variant_dir)
		self.post_build()
	def restore(self):
		try:
			env=ConfigSet.ConfigSet(os.path.join(self.cache_dir,'build.config.py'))
		except(IOError,OSError):
			pass
		else:
			if env['version']<Context.HEXVERSION:
				raise Errors.WafError('Version mismatch! reconfigure the project')
			for t in env['tools']:
				self.setup(**t)
		f=None
		try:
			try:
				f=open(os.path.join(self.variant_dir,Context.DBFILE),'rb')
			except(IOError,EOFError):
				Logs.debug('build: could not load the build cache (missing)')
			else:
				try:
					waflib.Node.pickle_lock.acquire()
					waflib.Node.Nod3=self.node_class
					try:
						data=cPickle.load(f)
					except Exception ,e:
						Logs.debug('build: could not load the build cache %r'%e)
					else:
						for x in SAVED_ATTRS:
							setattr(self,x,data[x])
				finally:
					waflib.Node.pickle_lock.release()
		finally:
			if f:
				f.close()
		self.init_dirs()
	def store(self):
		data={}
		for x in SAVED_ATTRS:
			data[x]=getattr(self,x)
		db=os.path.join(self.variant_dir,Context.DBFILE)
		try:
			waflib.Node.pickle_lock.acquire()
			waflib.Node.Nod3=self.node_class
			f=None
			try:
				f=open(db+'.tmp','wb')
				cPickle.dump(data,f)
			finally:
				if f:
					f.close()
		finally:
			waflib.Node.pickle_lock.release()
		try:
			st=os.stat(db)
			os.unlink(db)
			if sys.platform!='win32':
				os.chown(db+'.tmp',st.st_uid,st.st_gid)
		except(AttributeError,OSError):
			pass
		os.rename(db+'.tmp',db)
	def compile(self):
		Logs.debug('build: compile()')
		self.producer=Runner.Parallel(self,self.jobs)
		self.producer.biter=self.get_build_iterator()
		self.returned_tasks=[]
		try:
			self.producer.start()
		except KeyboardInterrupt:
			if self.producer.dirty:
				self.store()
			raise
		else:
			if self.producer.dirty:
				self.store()
		if self.producer.error:
			raise Errors.BuildError(self.producer.error)
	def setup(self,tool,tooldir=None,funs=None):
		if isinstance(tool,list):
			for i in tool:self.setup(i,tooldir)
			return
		module=Context.load_tool(tool,tooldir)
		if hasattr(module,"setup"):module.setup(self)
	def get_env(self):
		try:
			return self.all_envs[self.variant]
		except KeyError:
			return self.all_envs['']
	def set_env(self,val):
		self.all_envs[self.variant]=val
	env=property(get_env,set_env)
	def add_manual_dependency(self,path,value):
		if isinstance(path,waflib.Node.Node):
			node=path
		elif os.path.isabs(path):
			node=self.root.find_resource(path)
		else:
			node=self.path.find_resource(path)
		self.deps_man[id(node)].append(value)
	def launch_node(self):
		try:
			return self.p_ln
		except AttributeError:
			self.p_ln=self.root.find_dir(self.launch_dir)
			return self.p_ln
	def hash_env_vars(self,env,vars_lst):
		if not env.table:
			env=env.parent
			if not env:
				return Utils.SIG_NIL
		idx=str(id(env))+str(vars_lst)
		try:
			cache=self.cache_env
		except AttributeError:
			cache=self.cache_env={}
		else:
			try:
				return self.cache_env[idx]
			except KeyError:
				pass
		lst=[env[a]for a in vars_lst]
		ret=Utils.h_list(lst)
		Logs.debug('envhash: %r %r',ret,lst)
		cache[idx]=ret
		return ret
	def get_tgen_by_name(self,name):
		cache=self.task_gen_cache_names
		if not cache:
			for g in self.groups:
				for tg in g:
					try:
						cache[tg.name]=tg
					except AttributeError:
						pass
		try:
			return cache[name]
		except KeyError:
			raise Errors.WafError('Could not find a task generator for the name %r'%name)
	def progress_line(self,state,total,col1,col2):
		n=len(str(total))
		Utils.rot_idx+=1
		ind=Utils.rot_chr[Utils.rot_idx%4]
		pc=(100.*state)/total
		eta=str(self.timer)
		fs="[%%%dd/%%%dd][%%s%%2d%%%%%%s][%s]["%(n,n,ind)
		left=fs%(state,total,col1,pc,col2)
		right='][%s%s%s]'%(col1,eta,col2)
		cols=Logs.get_term_cols()-len(left)-len(right)+2*len(col1)+2*len(col2)
		if cols<7:cols=7
		ratio=((cols*state)//total)-1
		bar=('='*ratio+'>').ljust(cols)
		msg=Utils.indicator%(left,bar,right)
		return msg
	def declare_chain(self,*k,**kw):
		return TaskGen.declare_chain(*k,**kw)
	def pre_build(self):
		for m in getattr(self,'pre_funs',[]):
			m(self)
	def post_build(self):
		for m in getattr(self,'post_funs',[]):
			m(self)
	def add_pre_fun(self,meth):
		try:
			self.pre_funs.append(meth)
		except AttributeError:
			self.pre_funs=[meth]
	def add_post_fun(self,meth):
		try:
			self.post_funs.append(meth)
		except AttributeError:
			self.post_funs=[meth]
	def get_group(self,x):
		if not self.groups:
			self.add_group()
		if x is None:
			return self.groups[self.current_group]
		if x in self.group_names:
			return self.group_names[x]
		return self.groups[x]
	def add_to_group(self,tgen,group=None):
		assert(isinstance(tgen,TaskGen.task_gen)or isinstance(tgen,Task.TaskBase))
		tgen.bld=self
		self.get_group(group).append(tgen)
	def get_group_name(self,g):
		if not isinstance(g,list):
			g=self.groups[g]
		for x in self.group_names:
			if id(self.group_names[x])==id(g):
				return x
		return''
	def get_group_idx(self,tg):
		se=id(tg)
		for i in range(len(self.groups)):
			for t in self.groups[i]:
				if id(t)==se:
					return i
		return None
	def add_group(self,name=None,move=True):
		if name and name in self.group_names:
			Logs.error('add_group: name %s already present'%name)
		g=[]
		self.group_names[name]=g
		self.groups.append(g)
		if move:
			self.current_group=len(self.groups)-1
	def set_group(self,idx):
		if isinstance(idx,str):
			g=self.group_names[idx]
			for i in range(len(self.groups)):
				if id(g)==id(self.groups[i]):
					self.current_group=i
		else:
			self.current_group=idx
	def total(self):
		total=0
		for group in self.groups:
			for tg in group:
				try:
					total+=len(tg.tasks)
				except AttributeError:
					total+=1
		return total
	def get_targets(self):
		to_post=[]
		min_grp=0
		for name in self.targets.split(','):
			tg=self.get_tgen_by_name(name)
			if not tg:
				raise Errors.WafError('target %r does not exist'%name)
			m=self.get_group_idx(tg)
			if m>min_grp:
				min_grp=m
				to_post=[tg]
			elif m==min_grp:
				to_post.append(tg)
		return(min_grp,to_post)
	def post_group(self):
		if self.targets=='*':
			for tg in self.groups[self.cur]:
				try:
					f=tg.post
				except AttributeError:
					pass
				else:
					f()
		elif self.targets:
			if self.cur<self._min_grp:
				for tg in self.groups[self.cur]:
					try:
						f=tg.post
					except AttributeError:
						pass
					else:
						f()
			else:
				for tg in self._exact_tg:
					tg.post()
		else:
			ln=self.launch_node()
			for tg in self.groups[self.cur]:
				try:
					f=tg.post
				except AttributeError:
					pass
				else:
					if tg.path.is_child_of(ln):
						f()
	def get_tasks_group(self,idx):
		tasks=[]
		for tg in self.groups[idx]:
			if isinstance(tg,Task.TaskBase):
				tasks.append(tg)
			else:
				tasks.extend(tg.tasks)
		return tasks
	def get_build_iterator(self):
		self.cur=0
		if self.targets and self.targets!='*':
			(self._min_grp,self._exact_tg)=self.get_targets()
		global lazy_post
		if self.post_mode!=POST_LAZY:
			while self.cur<len(self.groups):
				self.post_group()
				self.cur+=1
			self.cur=0
		while self.cur<len(self.groups):
			if self.post_mode!=POST_AT_ONCE:
				self.post_group()
			tasks=self.get_tasks_group(self.cur)
			Task.set_file_constraints(tasks)
			Task.set_precedence_constraints(tasks)
			self.cur_tasks=tasks
			self.cur+=1
			if not tasks:
				continue
			yield tasks
		while 1:
			yield[]
class inst_task(Task.Task):
	color='CYAN'
	def post(self):
		buf=[]
		for x in self.source:
			if isinstance(x,waflib.Node.Node):
				y=x
			else:
				y=self.path.find_resource(x)
				if not y:
					idx=self.generator.bld.get_group_idx(self)
					for tg in self.generator.bld.groups[idx]:
						if not isinstance(tg,inst_task)and id(tg)!=id(self):
							tg.post()
						y=self.path.find_resource(x)
						if y:
							break
					else:
						raise Errors.WafError('could not find %r in %r'%(x,self.path))
			buf.append(y)
		self.inputs=buf
	def runnable_status(self):
		ret=super(inst_task,self).runnable_status()
		if ret==Task.SKIP_ME:
			return Task.RUN_ME
		return ret
	def __str__(self):
		return''
	def run(self):
		return self.generator.exec_task()
	def get_install_path(self):
		dest=self.dest.replace('/',os.sep)
		dest=Utils.subst_vars(self.dest,self.env)
		if Options.options.destdir:
			dest=os.path.join(Options.options.destdir,os.path.splitdrive(dest)[1].lstrip(os.sep))
		return dest
	def exec_install_files(self):
		destpath=self.get_install_path()
		if not destpath:
			raise Errors.WafError('unknown installation path %r'%self.generator)
		for x,y in zip(self.source,self.inputs):
			if self.relative_trick:
				destfile=os.path.join(destpath,y.path_from(self.path))
				Utils.check_dir(os.path.dirname(destfile))
			else:
				destfile=os.path.join(destpath,y.name)
			self.generator.bld.do_install(y.abspath(),destfile,self.chmod)
	def exec_install_as(self):
		destfile=self.get_install_path()
		self.generator.bld.do_install(self.inputs[0].abspath(),destfile,self.chmod)
	def exec_symlink_as(self):
		destfile=self.get_install_path()
		self.generator.bld.do_link(self.link,destfile)
class InstallContext(BuildContext):
	'''installs the targets on the system'''
	cmd='install'
	def __init__(self,**kw):
		super(InstallContext,self).__init__(**kw)
		self.uninstall=[]
		self.is_install=INSTALL
	def do_install(self,src,tgt,chmod=Utils.O644):
		d,_=os.path.split(tgt)
		if not d:
			raise Errors.WafError('Invalid installation given %r->%r'%(src,tgt))
		Utils.check_dir(d)
		srclbl=src.replace(self.srcnode.abspath()+os.sep,'')
		if not Options.options.force:
			try:
				st1=os.stat(tgt)
				st2=os.stat(src)
			except OSError:
				pass
			else:
				if st1.st_mtime>=st2.st_mtime and st1.st_size==st2.st_size:
					if not self.progress_bar:
						Logs.info('- install %s (from %s)'%(tgt,srclbl))
					return False
		if not self.progress_bar:
			Logs.info('+ install %s (from %s)'%(tgt,srclbl))
		try:
			os.remove(tgt)
		except OSError:
			pass
		try:
			shutil.copy2(src,tgt)
			os.chmod(tgt,chmod)
		except IOError:
			try:
				os.stat(src)
			except(OSError,IOError):
				Logs.error('File %r does not exist'%src)
			raise Errors.WafError('Could not install the file %r'%tgt)
	def do_link(self,src,tgt):
		d,_=os.path.split(tgt)
		Utils.check_dir(d)
		link=False
		if not os.path.islink(tgt):
			link=True
		elif os.readlink(tgt)!=src:
			link=True
		if link:
			try:os.remove(tgt)
			except OSError:pass
			if not self.progress_bar:
				Logs.info('+ symlink %s (to %s)'%(tgt,src))
			os.symlink(src,tgt)
		else:
			if not self.progress_bar:
				Logs.info('- symlink %s (to %s)'%(tgt,src))
	def run_task_now(self,tsk,postpone):
		tsk.post()
		if not postpone:
			if tsk.runnable_status()==Task.ASK_LATER:
				raise self.WafError('cannot post the task %r'%tsk)
			tsk.run()
	def install_files(self,dest,files,env=None,chmod=Utils.O644,relative_trick=False,cwd=None,add=True,postpone=True):
		tsk=inst_task(env=env or self.env)
		tsk.bld=self
		tsk.path=cwd or self.path
		tsk.chmod=chmod
		if isinstance(files,waflib.Node.Node):
			tsk.source=[files]
		else:
			tsk.source=Utils.to_list(files)
		tsk.dest=dest
		tsk.exec_task=tsk.exec_install_files
		tsk.relative_trick=relative_trick
		if add:self.add_to_group(tsk)
		self.run_task_now(tsk,postpone)
		return tsk
	def install_as(self,dest,srcfile,env=None,chmod=Utils.O644,cwd=None,add=True,postpone=True):
		tsk=inst_task(env=env or self.env)
		tsk.bld=self
		tsk.path=cwd or self.path
		tsk.chmod=chmod
		tsk.source=[srcfile]
		tsk.dest=dest
		tsk.exec_task=tsk.exec_install_as
		if add:self.add_to_group(tsk)
		self.run_task_now(tsk,postpone)
		return tsk
	def symlink_as(self,dest,src,env=None,cwd=None,add=True,postpone=True):
		if sys.platform=='win32':
			return
		tsk=inst_task(env=env or self.env)
		tsk.bld=self
		tsk.dest=dest
		tsk.path=cwd or self.path
		tsk.source=[]
		tsk.link=src
		tsk.exec_task=tsk.exec_symlink_as
		if add:self.add_to_group(tsk)
		self.run_task_now(tsk,postpone)
		return tsk
class UninstallContext(InstallContext):
	'''removes the targets installed'''
	cmd='uninstall'
	def __init__(self,**kw):
		super(UninstallContext,self).__init__(**kw)
		self.is_install=UNINSTALL
	def do_install(self,src,tgt,chmod=Utils.O644):
		if not self.progress_bar:
			Logs.info('- remove %s'%tgt)
		self.uninstall.append(tgt)
		try:
			os.remove(tgt)
		except OSError ,e:
			if e.errno!=errno.ENOENT:
				if not getattr(self,'uninstall_error',None):
					self.uninstall_error=True
					Logs.warn('build: some files could not be uninstalled (retry with -vv to list them)')
				if Logs.verbose>1:
					Logs.warn('could not remove %s (error code %r)'%(e.filename,e.errno))
		while tgt:
			tgt=os.path.dirname(tgt)
			try:
				os.rmdir(tgt)
			except OSError:
				break
	def do_link(self,src,tgt):
		try:
			if not self.progress_bar:
				Logs.info('- unlink %s'%tgt)
			os.remove(tgt)
		except OSError:
			pass
		while tgt:
			tgt=os.path.dirname(tgt)
			try:
				os.rmdir(tgt)
			except OSError:
				break
	def execute(self):
		try:
			def runnable_status(self):
				return Task.SKIP_ME
			setattr(Task.Task,'runnable_status_back',Task.Task.runnable_status)
			setattr(Task.Task,'runnable_status',runnable_status)
			super(UninstallContext,self).execute()
		finally:
			setattr(Task.Task,'runnable_status',Task.Task.runnable_status_back)
class CleanContext(BuildContext):
	'''cleans the project'''
	cmd='clean'
	def execute(self):
		self.restore()
		if not self.all_envs:
			self.load_envs()
		self.recurse([self.run_dir])
		try:
			self.clean()
		finally:
			self.store()
	def clean(self):
		Logs.debug('build: clean called')
		if self.bldnode!=self.srcnode:
			lst=[self.root.find_or_declare(f)for f in self.env[CFG_FILES]]
			for n in self.bldnode.ant_glob('**/*',excl='lock* *conf_check_*/** config.log c4che/*'):
				if n in lst:
					continue
				n.delete()
		self.root.children={}
		for v in'node_deps task_sigs raw_deps'.split():
			setattr(self,v,{})
class ListContext(BuildContext):
	'''lists the targets to execute'''
	cmd='list'
	def execute(self):
		self.restore()
		if not self.all_envs:
			self.load_envs()
		self.recurse([self.run_dir])
		self.pre_build()
		self.timer=Utils.Timer()
		for g in self.groups:
			for tg in g:
				try:
					f=tg.post
				except AttributeError:
					pass
				else:
					f()
		try:
			self.get_tgen_by_name('')
		except:
			pass
		lst=list(self.task_gen_cache_names.keys())
		lst.sort()
		for k in lst:
			Logs.pprint('GREEN',k)
class StepContext(BuildContext):
	'''executes tasks in a step-by-step fashion, for debugging'''
	cmd='step'
	def __init__(self,**kw):
		super(StepContext,self).__init__(**kw)
		self.files=Options.options.files
	def compile(self):
		if not self.files:
			Logs.warn('Add a pattern for the debug build, for example "waf step --files=main.c,app"')
			BuildContext.compile(self)
			return
		for g in self.groups:
			for tg in g:
				try:
					f=tg.post
				except AttributeError:
					pass
				else:
					f()
		for pat in self.files.split(','):
			inn=True
			out=True
			if pat.startswith('in:'):
				out=False
				pat=pat.replace('in:','')
			elif pat.startswith('out:'):
				inn=False
				pat=pat.replace('out:','')
			pat=re.compile(pat,re.M)
			for g in self.groups:
				for tg in g:
					if isinstance(tg,Task.TaskBase):
						lst=[tg]
					else:
						lst=tg.tasks
					for tsk in lst:
						do_exec=False
						if inn:
							for node in getattr(tsk,'inputs',[]):
								if pat.search(node.abspath()):
									do_exec=True
									break
						if out and not do_exec:
							for node in getattr(tsk,'outputs',[]):
								if pat.search(node.abspath()):
									do_exec=True
									break
						if do_exec:
							ret=tsk.run()
							Logs.info('%s -> %r'%(str(tsk),ret))
BuildContext.store=Utils.nogc(BuildContext.store)
BuildContext.restore=Utils.nogc(BuildContext.restore)
