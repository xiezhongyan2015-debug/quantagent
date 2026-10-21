"""Independent APFS file clones plus bounded, sparse array writes.

An unsupported filesystem falls back to the existing bounded copy. A clone is
never a hard link: it has a new inode and its writes must leave source identity
unchanged. Parent hashes are checked in the required patch/comparison pass.
"""
import ctypes
from . import bounded_tail_pipeline as p
from .merge_native_candidate_raw_panel import copy_patched as full_copy_patched


def clone_file(source,destination):
    source=p.Path(source);destination=p.Path(destination)
    assert source.is_file() and not source.is_symlink() and not destination.exists()
    if p.sys.platform!='darwin':return False
    libc=ctypes.CDLL(None,use_errno=True)
    clone=getattr(libc,'clonefile',None)
    if clone is None:return False
    clone.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_int];clone.restype=ctypes.c_int
    before=p.identity(source)
    result=clone(p.os.fsencode(source),p.os.fsencode(destination),0)
    assert p.identity(source)==before
    if result:
        assert not destination.exists();return False
    assert destination.stat().st_ino!=source.stat().st_ino and destination.stat().st_size==source.stat().st_size
    return True


def copy_patched(source,destination,expected_sha,patches):
    source=p.Path(source);destination=p.Path(destination);before=p.identity(source)
    temp=destination.with_suffix('.npy.tmp');assert not destination.exists() and not temp.exists()
    if not clone_file(source,temp):return full_copy_patched(source,destination,expected_sha,patches)
    original_digest=p.hashlib.sha256();new_digest=p.hashlib.sha256();written=0
    with source.open('rb') as original,temp.open('r+b') as target:
        version=p.np.lib.format.read_magic(original);assert version in [(1,0),(2,0)]
        reader=p.np.lib.format.read_array_header_1_0 if version==(1,0) else p.np.lib.format.read_array_header_2_0
        shape,fortran,dtype=reader(original);assert not fortran
        offset=original.tell();original.seek(0);prefix=original.read(offset);assert target.read(offset)==prefix
        original_digest.update(prefix);new_digest.update(prefix)
        row_bytes=int(p.np.prod(shape[1:]))*dtype.itemsize;rows_per_batch=max(1,32*1024**2//row_bytes)
        cell_bytes=int(p.np.prod(shape[2:]))*dtype.itemsize if len(shape)>=2 else dtype.itemsize
        for start in range(0,shape[0],rows_per_batch):
            length=min(rows_per_batch,shape[0]-start);raw=original.read(length*row_bytes);assert len(raw)==length*row_bytes;original_digest.update(raw)
            selected=[(r,c,v) for (r,c),v in patches.items() if start<=r<start+length]
            if selected:
                raw=bytearray(raw);view=p.np.frombuffer(raw,dtype).reshape((length,*shape[1:]))
                for row,column,value in selected:
                    assert 0<=column<shape[1];view[row-start,column]=value
                    encoded=p.np.asarray(view[row-start,column],dtype=dtype).tobytes();assert len(encoded)==cell_bytes
                    target.seek(offset+row*row_bytes+column*cell_bytes);target.write(encoded);written+=len(encoded)
                del view
            new_digest.update(raw)
        assert original.read(1)==b'';target.flush();p.os.fsync(target.fileno())
    assert p.identity(source)==before and original_digest.hexdigest()==expected_sha
    temp.replace(destination)
    return dict(path=str(destination),sha256=new_digest.hexdigest(),shape=list(shape),dtype=str(dtype),verified_identity=p.identity(destination),source_expected_sha256=expected_sha,
        source_digest_verified_during_required_copy=True,source_guard_unchanged=True,patched_stock_hours=len(patches),batch_byte_target=32*1024**2,
        independent_copy_on_write_clone=True,hard_link_used=False,logical_file_bytes=destination.stat().st_size,explicitly_written_changed_value_bytes=written)
