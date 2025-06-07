#!/usr/bin/python3
import docker
import argparse
import shutil
import signal
import time
import sys
import os

label_name = "hoster.domains"
enclosing_pattern = "#-----------Docker-Hoster-Domains----------\n"
enclosing_pattern_iptables = "#-----------Docker-Hoster-Rules----------\n"
hosts_path = "/tmp/hosts"
iptables_path = "/tmp/rules.v4"
hosts = {}
traefik_hosts = set()
iptables_rules = set()

def signal_handler(signal, frame):
    global hosts
    hosts = {}
    update_hosts_file()
    sys.exit(0)

def main():
    # register the exit signals
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    args = parse_args()
    global hosts_path
    hosts_path = args.file
    iptables_path = args.iptables_path

    dockerClient = docker.APIClient(base_url='unix://%s' % args.socket)
    events = dockerClient.events(decode=True)
    #get running containers
    for c in dockerClient.containers(quiet=True, all=False):
        container_id = c["Id"]
        container = get_container_data(dockerClient, container_id)
        hosts[container_id] = container

    update_hosts_file()
    update_iptables_file()

    #listen for events to keep the hosts file updated
    for e in events:
        if e["Type"]!="container": 
            continue
        
        status = e["status"]
        if status =="start":
            container_id = e["id"]
            container = get_container_data(dockerClient, container_id)
            hosts[container_id] = container
            update_hosts_file()
            update_iptables_file()

        if status=="stop" or status=="die" or status=="destroy":
            container_id = e["id"]
            if container_id in hosts:
                hosts.pop(container_id)
                update_hosts_file()

        if status=="rename":
            container_id = e["id"]
            if container_id in hosts:
                container = get_container_data(dockerClient, container_id)
                hosts[container_id] = container
                update_hosts_file()


def has_traefik_label(info):
    for label in info['Config']['Labels']:
        if label.startswith('traefik.http.routers'):
            return True
    return False


def get_container_data(dockerClient, container_id):
    #extract all the info with the docker api
    info = dockerClient.inspect_container(container_id)
    container_hostname = info["Config"]["Hostname"]
    container_name = info["Name"].strip("/")
    container_ip = info["NetworkSettings"]["IPAddress"]
    if info["Config"]["Domainname"]:
        container_hostname = container_hostname + "." + info["Config"]["Domainname"]
    if has_traefik_label(info):
        traefik_hosts.add(container_name)

    result = []

    for name, values in info["NetworkSettings"]["Networks"].items():
        network_info = dockerClient.inspect_network(name)
        if 'isolated-from-host' in network_info['Labels']:
            # Not reachable from host, so no need to add to hosts file
            gateway = network_info['IPAM']['Config'][0]['Gateway']
            interface = 'br-' + network_info['Id'][:12]
            iptables_rules.add(f"-A OUTPUT -s {gateway} -o {interface} -j DROP")
            continue
        
        if not values["Aliases"]: 
            continue

        result.append({
                "ip": values["IPAddress"] , 
                "name": container_name,
                "domains": set(values["Aliases"] + [container_name, container_hostname])
            })

    if container_ip:
        result.append({"ip": container_ip, "name": container_name, "domains": [container_name, container_hostname ]})

    return result


def update_hosts_file():
    if len(hosts)==0:
        print("Removing all hosts before exit...")
    else:
        print("Updating hosts file with:")

    for id,addresses in hosts.items():
        for addr in addresses:
            if addr['name'] == 'traefik':
                addr['domains'] |= traefik_hosts
            print("ip: %s domains: %s" % (addr["ip"], addr["domains"]))

    #read all the lines of thge original file
    lines = []
    with open(hosts_path,"r+") as hosts_file:
        lines = hosts_file.readlines()

    #remove all the lines after the known pattern
    for i,line in enumerate(lines):
        if line==enclosing_pattern:
            lines = lines[:i]
            break;

    #remove all the trailing newlines on the line list
    if lines:
        while lines[-1].strip()=="": lines.pop()

    #append all the domain lines
    if len(hosts)>0:
        lines.append("\n\n"+enclosing_pattern)
        
        for id, addresses in hosts.items():
            for addr in addresses:
                lines.append("%s    %s\n"%(addr["ip"],"   ".join(addr["domains"])))
        
        lines.append("#-----Do-not-add-hosts-after-this-line-----\n\n")

    #write it on the auxiliar file
    aux_file_path = hosts_path+".aux"
    with open(aux_file_path,"w") as aux_hosts:
        aux_hosts.writelines(lines)

    #replace etc/hosts with aux file, making it atomic
    shutil.move(aux_file_path, hosts_path)


def update_iptables_file():
    #read all the lines of thge original file
    lines = []
    with open(iptables_path,"r+") as hosts_file:
        lines = hosts_file.readlines()

    #remove all the lines after the known pattern
    for i,line in enumerate(lines):
        if line==enclosing_pattern_iptables:
            lines = lines[:i]
            break;

    #remove all the trailing newlines on the line list
    if lines:
        while lines and lines[-1].strip()=="": lines.pop()

    #append all the domain lines
    if len(hosts)>0:
        lines.append("\n\n"+enclosing_pattern_iptables)
        lines.append("*filter\n")
        
        for line in iptables_rules:
            lines.append(line + '\n')
        
        lines.append("COMMIT\n")
        lines.append("#-----Do-not-add-rules-after-this-line-----\n\n")

    #write it on the auxiliar file
    aux_file_path = iptables_path+".aux"
    with open(aux_file_path,"w") as aux_hosts:
        aux_hosts.writelines(lines)

    #replace etc/hosts with aux file, making it atomic
    shutil.move(aux_file_path, iptables_path)

def parse_args():
    parser = argparse.ArgumentParser(description='Synchronize running docker container IPs with host /etc/hosts file.')
    parser.add_argument('socket', type=str, nargs="?", default="tmp/docker.sock", help='The docker socket to listen for docker events.')
    parser.add_argument('file', type=str, nargs="?", default=hosts_path, help='The /etc/hosts file to sync the containers with.')
    parser.add_argument('iptables_path', type=str, nargs='?', default=iptables_path, help='The iptables rules file to sync the containers with.')
    return parser.parse_args()

if __name__ == '__main__':
    main()

