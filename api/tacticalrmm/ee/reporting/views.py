"""
Copyright (c) 2023-present Amidaware Inc.
This file is subject to the EE License Agreement.
For details, see: https://license.tacticalrmm.com/ee
"""

from rest_framework.response import Response
from rest_framework.request import Request
from rest_framework.views import APIView
from rest_framework.serializers import (
    Serializer,
    ModelSerializer,
    CharField,
    ListField,
    ValidationError,
)
from typing import Union, List
from django.core.exceptions import (
    SuspiciousFileOperation,
    ObjectDoesNotExist,
    PermissionDenied,
)
from django.core.files.base import ContentFile
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404

import os
import shutil

from .storage import report_assets_fs
from .models import ReportTemplate, ReportAsset, ReportHTMLTemplate, ReportDataQuery
from .utils import generate_html, generate_pdf
from tacticalrmm.utils import notify_error

def path_exists(value: str) -> None:
    if not report_assets_fs.exists(value):
        raise ValidationError("Path does not exist on the file system")


class ReportTemplateSerializer(ModelSerializer[ReportTemplate]):
    class Meta:
        model = ReportTemplate
        fields = "__all__"


class GetAddReportTemplate(APIView):
    queryset = ReportTemplate.objects.all()
    serializer_class = ReportTemplateSerializer

    def get(self, request: Request) -> Response:
        reports = ReportTemplate.objects.all()
        return Response(ReportTemplateSerializer(reports, many=True).data)

    def post(self, request: Request) -> Response:
        serializer = ReportTemplateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = serializer.save()

        return Response(ReportTemplateSerializer(response).data)


class GetEditDeleteReportTemplate(APIView):
    queryset = ReportTemplate.objects.all()
    serializer_class = ReportTemplateSerializer

    def get(self, request: Request, pk: int) -> Response:
        template = get_object_or_404(ReportTemplate, pk=pk)

        return Response(ReportTemplateSerializer(template).data)

    def put(self, request: Request, pk: int) -> Response:
        template = get_object_or_404(ReportTemplate, pk=pk)

        serializer = ReportTemplateSerializer(
            instance=template, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        response = serializer.save()

        return Response(ReportTemplateSerializer(response).data)

    def delete(self, request: Request, pk: int) -> Response:
        get_object_or_404(ReportTemplate, pk=pk).delete()

        return Response()


class GenerateSavedReport(APIView):
    def post(self, request: Request, pk: int) -> Union[FileResponse, Response]:
        template = get_object_or_404(ReportTemplate, pk=pk)

        html_report = generate_html(
            template=template.template_md,
            template_type=template.type,
            css=template.template_css if template.template_css else "",
            html_template=template.template_html.html
            if template.template_html
            else None,
        )

        pdf_bytes = generate_pdf(html=html_report)

        return FileResponse(
            ContentFile(pdf_bytes),
            content_type="application/pdf",
            filename=f"{template.name}.pdf",
        )


class GenerateReportPreview(APIView):
    def post(self, request: Request) -> Union[FileResponse, Response]:
        template_md = request.data["template_md"]
        template_css = request.data["template_css"]
        template_type = request.data["type"]
        template_html = (
            request.data["template_html"]
            if "template_html" in request.data.keys()
            else None
        )
        
        html_report = generate_html(
            template=request.data["template_md"] if template_type == "markdown" else request.data["template_html"],
            template_type=request.data["type"],
            css=template_css,
            html_template=template_html,
            variables=request.data["template_variables"],
        )

        response_format = request.data["format"]
        
        if response_format == "html":
            return Response(html_report)
        else:
            pdf_bytes = generate_pdf(html=html_report)

            return FileResponse(
                ContentFile(pdf_bytes),
                content_type="application/pdf",
                filename=f"preview.pdf",
            )


class GetReportAssets(APIView):
    def get(self, request: Request) -> Response:
        path = request.query_params.get("path", "").lstrip("/")

        directories, files = report_assets_fs.listdir(path)
        response = list()

        # parse directories
        for foldername in directories:
            relpath = os.path.join(path, foldername)
            response.append(
                {
                    "name": foldername,
                    "path": relpath,
                    "type": "folder",
                    "size": None,
                    "url": report_assets_fs.url(relpath),
                }
            )

        # parse files
        for filename in files:
            relpath = os.path.join(path, filename)
            response.append(
                {
                    "name": filename,
                    "path": relpath,
                    "type": "file",
                    "size": str(report_assets_fs.size(relpath)),
                    "url": report_assets_fs.url(relpath),
                }
            )

        return Response(response)


class GetAllAssets(APIView):
    def get(self, request: Request) -> Response:
        only_folders = request.query_params.get("OnlyFolders", None)
        only_folders = True if only_folders and only_folders == "true" else False

        response = {}

        # recursively loop over report assets and add them to response
        try:
            os.chdir(report_assets_fs.base_location)
        except FileNotFoundError:
            return notify_error("Unable to process request")

        for current_dir, subdirs, files in os.walk("."):
            nodes = list()

            for dirname in subdirs:
                nodes.append(
                    {
                        "type": "folder",
                        "name": dirname,
                        "path": f"{current_dir}/{dirname}",
                    }
                )

            if not only_folders:
                for filename in files:
                    nodes.append(
                        {
                            "type": "file",
                            "name": filename,
                            "path": f"{current_dir}/{filename}",
                        }
                    )

            response[current_dir] = nodes

        return Response(response)


class RenameReportAsset(APIView):
    class InputRequest:
        path: str
        newName: str

    class InputSerializer(Serializer[InputRequest]):
        path = CharField(required=True, validators=[path_exists])
        newName = CharField(required=True)

    def put(self, request: Request) -> Response:
        serializer = self.InputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_path = serializer.data["path"]
        new_name = serializer.data["newName"]

        # make sure absolute path isn't processed
        old_path = old_path.lstrip("/") if old_path else ""

        try:
            name = report_assets_fs.rename(path=old_path, new_name=new_name)

            if report_assets_fs.isfile(path=name):
                asset = ReportAsset.objects.get(file=old_path)
                asset.file.name = name
                asset.save()

            return Response(name)
        except OSError as error:
            return notify_error(str(error))
        except SuspiciousFileOperation as error:
            return notify_error(str(error))


class CreateAssetFolder(APIView):
    def post(self, request: Request) -> Response:
        path = request.data["path"].lstrip("/") if "path" in request.data else ""

        try:
            new_path = report_assets_fs.createfolder(path=path)
            return Response(new_path)
        except OSError as error:
            return notify_error(str(error))
        except SuspiciousFileOperation as error:
            return notify_error(str(error))


class DeleteAssets(APIView):
    class InputRequest:
        paths: List[str]

    class InputSerializer(Serializer[InputRequest]):
        paths = ListField(required=True)

    def post(self, request: Request) -> Response:
        serializer = self.InputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        paths = serializer.data["paths"]

        try:
            for path in paths:
                path = path.lstrip("/") if path else ""
                if report_assets_fs.isdir(path=path):
                    shutil.rmtree(report_assets_fs.path(path))
                    ReportAsset.objects.filter(file__startswith=f"{path}/").delete()
                else:
                    try:
                        asset = ReportAsset.objects.get(file=path)
                        asset.file.delete()
                        asset.delete()
                    except ObjectDoesNotExist:
                        report_assets_fs.delete(path)

            return Response()

        except OSError as error:
            return notify_error(str(error))
        except SuspiciousFileOperation as error:
            return notify_error(str(error))


class UploadAssets(APIView):
    def post(self, request: Request) -> Response:
        path = (
            request.data["parentPath"].lstrip("/")
            if "parentPath" in request.data
            else ""
        )

        try:
            response = {}

            # make sure this is actually a directory
            if report_assets_fs.isdir(path=path):
                for filename in request.FILES:
                    asset = ReportAsset(file=request.FILES[filename])
                    asset.file.name = os.path.join(path, filename)
                    asset.save()

                    asset.refresh_from_db()

                    response[filename] = {
                        "id": asset.id,
                        "filename": asset.file.name,
                    }

                return Response(response)
            else:
                return notify_error("parentPath doesn't point to a directory")

        except OSError as error:
            return notify_error(str(error))
        except SuspiciousFileOperation as error:
            return notify_error(str(error))


class DownloadAssets(APIView):
    def get(self, request: Request) -> Union[Response, FileResponse]:
        path = request.query_params.get("path", "")

        # make sure absolute path isn't processed
        path = path.lstrip("/") if path else ""

        try:
            full_path = report_assets_fs.path(name=path)
            if report_assets_fs.isdir(path=path):
                zip_path = shutil.make_archive(
                    base_name=f"{report_assets_fs.path(name=path)}.zip",
                    format="zip",
                    root_dir=full_path,
                )

                response = FileResponse(
                    open(zip_path, "rb"),
                    as_attachment=True,
                    filename=zip_path.split("/")[-1],
                )

                os.remove(zip_path)

                return response
            else:
                return FileResponse(
                    open(full_path, "rb"),
                    as_attachment=True,
                    filename=full_path.split("/")[-1],
                )

        except OSError as error:
            return notify_error(str(error))
        except SuspiciousFileOperation as error:
            return notify_error(str(error))


class MoveAssets(APIView):
    class InputRequest:
        srcPaths: List[str]
        destination: str

    class InputSerializer(Serializer[InputRequest]):
        srcPaths = ListField(required=True)
        destination = CharField(required=True, validators=[path_exists])

    def post(self, request: Request) -> Response:
        serializer = self.InputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        paths = serializer.data["srcPaths"]
        destination = serializer.data["destination"]

        try:
            response = {}
            for path in paths:
                new_path = report_assets_fs.move(source=path, destination=destination)

                response["path"] = new_path

            return Response(response)

        except OSError as error:
            return notify_error(str(error))
        except SuspiciousFileOperation as error:
            return notify_error(str(error))


class ReportHTMLTemplateSerializer(ModelSerializer[ReportHTMLTemplate]):
    class Meta:
        model = ReportHTMLTemplate
        fields = "__all__"


class GetAddReportHTMLTemplate(APIView):
    def get(self, request: Request) -> Response:
        reports = ReportHTMLTemplate.objects.all()
        return Response(ReportHTMLTemplateSerializer(reports, many=True).data)

    def post(self, request: Request) -> Response:
        serializer = ReportHTMLTemplateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = serializer.save()

        return Response(ReportHTMLTemplateSerializer(response).data)


class GetEditDeleteReportHTMLTemplate(APIView):
    def get(self, request: Request, pk: int) -> Response:
        template = get_object_or_404(ReportHTMLTemplate, pk=pk)

        return Response(ReportHTMLTemplateSerializer(template).data)

    def put(self, request: Request, pk: int) -> Response:
        template = get_object_or_404(ReportHTMLTemplate, pk=pk)

        serializer = ReportHTMLTemplateSerializer(
            instance=template, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        response = serializer.save()

        return Response(ReportHTMLTemplateSerializer(response).data)

    def delete(self, request: Request, pk: int) -> Response:
        get_object_or_404(ReportHTMLTemplate, pk=pk).delete()

        return Response()


class ReportDataQuerySerializer(ModelSerializer[ReportDataQuery]):
    class Meta:
        model = ReportDataQuery
        fields = "__all__"


class GetAddReportDataQuery(APIView):
    def get(self, request: Request) -> Response:
        reports = ReportDataQuery.objects.all()
        return Response(ReportDataQuerySerializer(reports, many=True).data)

    def post(self, request: Request) -> Response:
        serializer = ReportDataQuerySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = serializer.save()

        return Response(ReportDataQuerySerializer(response).data)


class GetEditDeleteReportDataQuery(APIView):
    def get(self, request: Request, pk: int) -> Response:
        template = get_object_or_404(ReportDataQuery, pk=pk)

        return Response(ReportDataQuerySerializer(template).data)

    def put(self, request: Request, pk: int) -> Response:
        template = get_object_or_404(ReportDataQuery, pk=pk)

        serializer = ReportDataQuerySerializer(
            instance=template, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        response = serializer.save()

        return Response(ReportDataQuerySerializer(response).data)

    def delete(self, request: Request, pk: int) -> Response:
        get_object_or_404(ReportDataQuery, pk=pk).delete()

        return Response()


def redirect_assets_to_nginx_if_authenticated(request: Request, path: str) -> HttpResponse:
    if request.user.is_authenticated:
        response = HttpResponse()
        response["X-Accel-Redirect"] = "/assets/" + path
        return response
    else:
        raise PermissionDenied()
