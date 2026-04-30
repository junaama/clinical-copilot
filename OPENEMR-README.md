# Setup & Development

## Local Setup

- Ensure git, docker is installed on your machine

### Git, Docker Setup

### OpenEmr CLI Setup

1. Copy the script to local linux environment, create the bin directory if it does not exist. (May have to use ~/.local/bin for newer versions of Ubuntu in steps 1 and 2 so the script is found in $PATH.)

```
mkdir ~/bin
curl -L https://raw.githubusercontent.com/openemr/openemr-devops/master/utilities/openemr-cmd/openemr-cmd > ~/bin/openemr-cmd
curl -L https://raw.githubusercontent.com/openemr/openemr-devops/master/utilities/openemr-cmd/openemr-cmd-h > ~/bin/openemr-cmd-h
```

2. Apply executable permissions to the script.

```
chmod +x ~/bin/openemr-cmd
chmod +x ~/bin/openemr-cmd-h
```

3. Test the installation.

```
# openemr-cmd
Usage: openemr-cmd COMMAND [ARGS]
Commands:
  --help                       Show the commands usage
  --version                    Show the openemr-cmd command version


# openemr-cmd-h
To search the keyword from openemr-cmd -h output quickly
  Usage: openemr-cmd-h keyword
  e.g.   openemr-cmd-h ssl
  h                         openemr-cmd -h
  docker                    docker-management
  php                       php-management
  test                      test-management
  sweep                     sweep-management
  reset                     reset-management
  backup                    backup-management
  ssl                       ssl-management
  mul                       multisite-management
  api                       api-management
  com                       computational-health-informatics
  webroot                   webroot-management
  others                    others
  keyword                   grep from openemr-cmd -h

# openemr-cmd-h test
test-management:
  ut, unit-test                      To run unit testing
  at, api-test                       To run api testing
  et, e2e-test                       To run e2e testing
  st, services-test                  To run services testing
  ft, fixtures-test                  To run fixtures testing
  vt, validators-test                To run validators testing
  ct, controllers-test               To run controllers testing
  ctt, common-test                   To run common testing
```

4. Setup Path

```
export PATH=~/bin:$PATH
```

### 
1. Navigate to `docker/development-easy`
 
```
cd docker/development-easy/
```

2. Start Docker compose

```
openemr-cmd up
```
Check status after using `docker ps -a`

```
openemr-cmd dl
```

To stop docker

```
openemr-cmd stop
```


5. Test setup

Navigate to localhost:8300
username: admin
password: pass

Navigate to localhost:8310
username: root
password: root

6. Add random patients


