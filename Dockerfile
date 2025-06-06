FROM python:3.13.2-alpine

RUN pip install docker
RUN mkdir /hoster
WORKDIR /hoster
ADD hoster.py /hoster/

CMD ["python", "-u", "hoster.py"]
