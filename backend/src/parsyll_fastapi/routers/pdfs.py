import mimetypes
from fastapi import APIRouter, Request, UploadFile, File, Form, Depends
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.exceptions import HTTPException
from uuid import uuid4
from database import db, auth, bucket
from auth.auth_bearer import JWTBearer
from auth.auth_handler import getUIDFromAuthorizationHeader
from models.model import User, Course

router = APIRouter(
    prefix="/pdfs",
    tags=["pdfs"]
)

## Download File endpoints

# This endpoint can be used to download any file
@router.get("/admin/{file_id}")
async def get_file(file_id: str):
    blob = bucket.get_blob(file_id)
    contents = blob.download_as_bytes()

    # filename for pdf download
    filename = file_id
    if 'filename' in blob.metadata:
        filename = blob.metadata['filename']
    else:
        file_ext = mimetypes.guess_extension(blob.content_type)
        filename += file_ext
    
    # TODO error handling for bucket.get_blob with no valid file_id

    return Response(content=contents, media_type=blob.content_type, headers={"Content-Disposition": f"attachment;filename={filename}"})


# This endpoint can only download file user:uid owns
# user download file (should only allow download for files associated with user)
# TODO get uid from JWT token instead of path
@router.get("/{uid}/{file_id}", dependencies=[Depends(JWTBearer())])
async def user_get_file(uid: str, file_id: str):
    user_doc_ref = db.collection(u'users').document(uid)
    user_doc = user_doc_ref.get()

    if not user_doc.exists:
        raise HTTPException(404, detail=f"User {uid} does not exist")

    courses_ref = user_doc_ref.collection(u'courses')
    syllabus_query = courses_ref.where(u'syllabus', u'==', str(file_id)) 

    # check if file_id belongs to this user
    if len(syllabus_query.get()) <= 0:
        raise HTTPException(404, detail=f"File {file_id} does not exist for user {uid}")

    
    blob = bucket.get_blob(file_id)
    contents = blob.download_as_bytes()

    # filename for pdf download
    filename = file_id
    if 'filename' in blob.metadata:
        filename = blob.metadata['filename']
    else:
        file_ext = mimetypes.guess_extension(blob.content_type)
        filename += file_ext

    # TODO error handling for bucket.get_blob with no valid file_id
     
    return Response(content=contents, media_type=blob.content_type, headers={"Content-Disposition": f"attachment;filename={filename}"})

## Upload file endpoints

# user upload file
@router.post("/submit", dependencies=[Depends(JWTBearer())])
async def user_upload_file( file: UploadFile, uid = Depends(getUIDFromAuthorizationHeader)):

    try: 
        user = auth.get_user(uid)
    except auth.UserNotFoundError:
        raise HTTPException(404, detail=f"User {uid} not found")
    
    user_doc_ref = db.collection(u'users').document(user.uid)
    user_doc = user_doc_ref.get()

    if not user_doc.exists:
        raise HTTPException(404, detail=f"User {uid} does not exist")

    file_contents = await file.read() 
    file_id = uuid4()

    blob = bucket.blob(str(file_id))
    # add user in metadata to associate file with user
    blob.metadata = {'Content-Type': file.content_type, 'filename': file.filename, 'user': user.uid}
    blob.upload_from_string(file_contents, content_type=file.content_type)

    course = Course(syllabus=str(file_id))
    user_doc_ref.collection(u'courses').add(course.__dict__)

    #### MAYBE WE SHOULD RETURN DIC WITH FILE.FILENAME ###### 
    return f"Uploaded {file.filename} for user {user.uid}"

## DELETE file endpoints

# Delete file: fileid 
@router.delete("/file/{file_id}", dependencies=[Depends(JWTBearer())])
async def delete_file(file_id: str, uid=Depends(getUIDFromAuthorizationHeader)):
    blob = bucket.get_blob(file_id)
    
    if 'user' in blob.metadata:
        uid = blob.metadata['user']
        user_doc_ref = db.collection(u'users').document(uid)
        courses_ref = user_doc_ref.collection(u'courses')
        syllabus_query = courses_ref.where(u'syllabus', u'==', str(file_id))

        for course_doc in syllabus_query.get():
            courses_ref.document(course_doc.id).update({
                u'syllabus': ""
            })


    delete_blob(file_id)

    return f"Deleted file {file_id}"

# Delete all file of user:uid
@router.delete("/user/{uid}")
async def delete_user_files(uid: str): 
    user_doc_ref = db.collection(u'users').document(uid)
    user_doc = user_doc_ref.get()

    if not user_doc.exists:
        raise HTTPException(404, detail=f"User {uid} does not exist")

    courses_ref = user_doc_ref.collection(u'courses')
    courses_docs = courses_ref.stream()
    
    user_syllabus_list = []
    for course_doc in courses_docs:
        user_syllabus_list.append(course_doc.get(u'syllabus'))
        courses_ref.document(course_doc.id).update({
            u'syllabus': ""
        })
    

    for file_id in user_syllabus_list:
        if file_id != "" and file_id != None:
            delete_blob(file_id)
    

    return f"Deleted all files associated with User {uid}"

# Delete file:file_id of user:uid
@router.delete("/user/{uid}/{file_id}")
async def delete_user_file(uid: str, file_id: str):
    user_doc_ref = db.collection(u'users').document(uid)
    user_doc = user_doc_ref.get()

    if not user_doc.exists:
        raise HTTPException(404, detail=f"User {uid} does not exist")
    
    courses_ref = user_doc_ref.collection(u'courses')
    syllabus_query = courses_ref.where(u'syllabus', u'==', str(file_id))

    if len(syllabus_query.get()) <= 0:
        raise HTTPException(404, detail=f"File {file_id} does not exist for user {uid}")

    # There should only be one doc corresponding to file_id, but for loop just incase
    for course_doc in syllabus_query.get():
        courses_ref.document(course_doc.id).update({
            u'syllabus': ""
        })
    
    delete_blob(file_id)
 
    return f"Deleted file {file_id} from User {uid}"

# CAREFUL WITH THIS ENDPOINT: Delete all files in storage bucket and associated users' db entries
# Remove this endpoint for prod
@router.delete("/delete_all")
async def delete_all_file_in_firebase():
    # print(bucket.list_blobs)
    for blob in bucket.list_blobs():
        delete_blob(blob.name)
    
    for user in auth.list_users().iterate_all():
        user_doc_ref = db.collection(u'users').document(user.uid)
        user_doc_ref.update({
            u'syllabus': []
        })

    return "Deleted all files in storage bucket!"



# Helper functions
def delete_blob(blob_name):
    """Deletes a blob from the bucket."""

    blob = bucket.blob(blob_name)
    generation_match_precondition = None

    # Optional: set a generation-match precondition to avoid potential race conditions
    # and data corruptions. The request to delete is aborted if the object's
    # generation number does not match your precondition.
    blob.reload()  # Fetch blob metadata to use in generation_match_precondition.
    generation_match_precondition = blob.generation

    blob.delete(if_generation_match=generation_match_precondition)

    print(f"Blob {blob_name} deleted.")